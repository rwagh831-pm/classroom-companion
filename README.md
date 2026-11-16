# Classroom Companion

A Telegram bot + Mini App that helps a teacher assign work, follow up, collect submissions, and — the part that matters — **see who needs attention today** without reading forty chat messages.

Built as a take-home for Super Intelli Machines (Product Manager, AI/Platform). Pilot scope: one school, one class (8A), one teacher, two students.

- Bot: `@ccGreenfieldBot`
- Mini App: served by `app.py`, exposed through a cloudflared tunnel (URL changes per run — see Runbook)
- Demo video (7:48): https://youtu.be/KkCTe_1Sp3w
- Companion docs: [PRD.md](PRD.md) · [IMPLEMENTATION_NOTE.md](IMPLEMENTATION_NOTE.md)

---

## 1. The product in one paragraph

Teachers already assign homework on WhatsApp. What they cannot do is answer, at 7 am, *"who do I need to talk to today?"* — and they cannot tell *"did not see it"* from *"did not do it"*. Classroom Companion keeps the chat where students already are (Telegram) and adds one structured surface for the teacher: an **Attention Board** that ranks students by risk (blocked › overdue › silent › on track) with the one thing to do about each.

**Chat = act in the moment. App = see the state.** A student saying "I'm stuck on Q5" belongs in chat. Thirty students' statuses belong on a screen, not in a scroll.

---

## 2. Architecture

```
  Teacher phone (Telegram)            Student phone (Telegram)
          │  text / buttons / photos          │
          └──────────────┬────────────────────┘
                         ▼
               Telegram Bot API (long polling)
                         │ getUpdates / sendMessage / editMessageText
                         ▼
   ┌──────────────── bot.py ────────────────┐      ┌────── reminders.py ──────┐
   │ identity (from.id → users)              │      │ rules pick who + kind     │
   │ linking by invite code                  │      │ model writes the sentence │
   │ teacher: parse → preview → confirm      │◄────►│ quiet hours, idempotent   │
   │ student: intent → status → ping teacher │      │ trigger: /remind [force]  │
   └───────┬──────────────┬──────────────────┘      └─────────────┬─────────────┘
           │              │                                       │
           ▼              ▼                                       │
     brain.py        db.py (SQLite: classroom.db) ◄───────────────┘
   (the only file   schools · classes · users · memberships · invites
    that calls the  assignments · assignment_students (per-student state)
    language model) submissions · feedback · reminders · processed_updates · events (the log)
                          ▲
                          │ same database, same rules
   ┌──────────────── app.py (FastAPI) ────────────────┐
   │ verifies Telegram initData (HMAC) on the server   │
   │ GET /api/me · /api/teacher · /api/student         │◄── Mini App page (vanilla JS)
   │ POST /api/feedback → Telegram message to student  │    inside Telegram, via HTTPS tunnel
   └───────────────────────────────────────────────────┘
```

Files:

| File | Role |
|---|---|
| `bot.py` | Telegram transport + business logic for chat. Polling loop at the bottom, resilient to network errors and bad updates. |
| `brain.py` | **Every language-model call lives here.** Four functions, each returning Pydantic-validated JSON with a confidence score. Swappable. |
| `db.py` | Schema, seed data, `connect()` context manager, `log_event()` (observable log), idempotency helper. |
| `app.py` | Mini App server: identity verification, access-filtered queries, Attention Board rules, feedback endpoint, the page itself. |
| `reminders.py` | Reminder policy (pure rules, tested) + the job. |
| `tg.py` | Shared `send_message` for modules that are not the bot. |
| `tests.py` | 14 tests of the rules and trust boundaries. No network, no API key. |
| `check_bot.py`, `echo_bot.py`, `watch_updates.py` | Day-1 learning/diagnostic scripts, kept for history. |

---

## 3. Runbook (local, as used for the demo)

Prerequisites: Python 3.11+, a Telegram bot token from @BotFather, an Anthropic API key, `cloudflared`.

```bash
pip install -r requirements.txt
copy .env.example .env        # then fill BOT_TOKEN, ANTHROPIC_API_KEY (and optionally SEED_TEACHER_TELEGRAM_ID)
python db.py                  # creates classroom.db with the pilot seed
python tests.py               # 14 tests, ~0.2 s
```

**One click:** double-click `start.bat` (or `python start.py`). It creates/updates the database, starts the Mini App server and the bot in their own windows, opens a cloudflared tunnel, and writes the tunnel URL into `.env` as `MINI_APP_URL` automatically. Close the launcher window to stop everything. **Restart path:** close it, run it again — all state is in `classroom.db`.

Manually, the same three processes:

```bash
python bot.py
```
```bash
python -m uvicorn app:app --port 8000 --reload
```
```bash
cloudflared tunnel --url http://localhost:8000
```

(then copy the `https://….trycloudflare.com` URL into `.env` as `MINI_APP_URL` and restart `bot.py`). The URL changes every time cloudflared restarts (quick tunnels are anonymous) — a named tunnel or a real host fixes this in production.

**Reset to a clean pilot:** stop `bot.py`, delete `classroom.db`, `python db.py`. If `SEED_TEACHER_TELEGRAM_ID` is set, the teacher stays linked. (Production would use migrations, not a reset.)

### Test accounts (seeded)

| Person | Role | Invite code | How they link |
|---|---|---|---|
| Priya (Maths) | teacher, 8A | `T-8A-PRIYA` | send the code to the bot (or pre-linked via `.env`) |
| Aarav | student, 8A | `S-8A-AARAV` | send the code to the bot |
| Diya | student, 8A | `S-8A-DIYA` | intentionally **left unlinked** in the demo |

**Nobody needs to know a command.** An unknown person is asked for their invite code; after linking, persistent buttons appear under the typing box (📊 Dashboard · 📝 Summary · ⏰ Run reminders · ❓ Help for teachers; 📊 Dashboard · ❓ Help for students), every assignment message carries ✅ Got it / 🙋 I need help buttons, and the `/` menu is registered with Telegram. Commands still work for the curious: `/start` · `/app` · `/summary` · `/remind` (`/remind force` ignores quiet hours and silence thresholds for demos) · `/help`. Everything else is plain language — including questions like *"who is at risk this week?"*.

---

## 4. The models the brief asked for

### 4.1 Access model

- **A person is not a role.** `users` holds people; `memberships(user_id, class_id, role, subject)` holds what they may do, scoped to a class. One person can be a teacher in 8A and nothing in 8B.
- **Server-side checks on every request.** The bot routes by membership (`teacher_classes`, `students_in_class`). The Mini App API filters every query by the caller's class ids (`a.class_id IN (…)`) and, for students, by `student_id = me`. `POST /api/feedback` verifies the caller teaches that class (403) and that the student is on that assignment (404).
- **Unauthorised attempts are rejected safely**: wrong invite code → "not valid"; reused code → "already used"; forged/absent Mini App signature → 401; valid signature but not linked → 403; opening the Mini App URL in a normal browser → "Access denied".

### 4.2 Identity model

- Telegram's `from.id` is the only identity we trust. Display names are never used for lookup (anyone can change their name).
- `users.telegram_id` starts **empty**. "Not linked yet" is a real state the teacher can see on the board ("cannot see assignments").
- Linking is by **per-person, class-scoped, single-use invite code**. The code decides who you are, not your Telegram profile.
- The Mini App gets identity from Telegram's signed `initData`: the server recomputes the HMAC-SHA256 with the bot token and compares in constant time, then looks the id up in `users`.

### 4.3 State model (smaller than the brief's full list, on purpose)

Per student, per assignment (`assignment_students.status`):

```
assigned ──► acknowledged ──► in_progress ──► submitted ──► completed
    │              │               │  ▲
    └──────────────┴──► blocked ───┘  │ (teacher: "needs changes")
                                      └─────────── from submitted
```

- `overdue` is **derived** from `due_at` vs now, never stored — so a deadline change cannot leave stale flags.
- `cancelled` exists in the schema; cancelling is not exposed in the pilot UI.
- Dropped for the pilot and why: `draft` (adds a teacher editing flow that does not help prove the core loop) and `needs_revision` as a separate state ("needs changes" feedback simply moves the student back to `in_progress`, which is what the board needs to show).
- Rule: a status **never moves backwards from `submitted`/`completed`** because of a student message. Only teacher feedback can.
- Deadline change: students who had only `acknowledged` go back to `assigned` so the board shows who has not seen the new date. `in_progress`/`blocked`/`submitted` are untouched — they are already engaged.

### 4.4 Reminder policy

| Student state | Who gets the message | Kind |
|---|---|---|
| `blocked` | the **teacher** ("Aarav is still blocked…") — nagging a blocked child helps no one | `blocked_teacher` |
| `assigned` for ≥ 1 h (silent) | the student: "did you see it? reply ok" | `not_seen` |
| due within 24 h, not submitted | the student | `due_soon` |
| past due, not submitted | the student (kindly) **and** the teacher | `overdue` |
| `submitted` / `completed` / `cancelled` | nobody | — |

- **Rules decide who and which kind** (`reminders.decide`, pure function, tested). **The model only writes the sentence**, with a plain template fallback if it fails.
- **Quiet hours 21:00–07:00 IST**: code defers, logs `reminder_deferred`. The model never sees the clock.
- **Idempotent**: `reminders(assignment_id, student_id, kind)` is UNIQUE — rerunning the job sends nothing twice. State is in SQLite, so a restart loses nothing.
- Triggers: `/remind` (or the ⏰ button) in chat — manual, as the brief requires; **⏰ Remind now** on any assignment in the Mini App (teacher acts from where she sees the state; runs the same rules for that one assignment, ignores quiet hours because it is an explicit human action); `python reminders.py loop` for the scheduled job (every 10 min). Changing the deadline clears `due_soon`/`overdue` rows so they can fire again for the new date.

### 4.5 LLM boundary — what the model decides vs what code decides

| Model decides (language, fuzzy) | Code decides (facts, safety, money-like) |
|---|---|
| "Do ex 4.2 q1-8, bring it day after" → title, instructions, due datetime, confidence | whether to save it (only after Pydantic validation + confidence ≥ 0.7 + due date in the future) |
| "move it to Friday evening" → this is a *change*, new datetime | which assignment (latest open), what to reset, who to notify |
| "no book, cant do" → intent `blocked`, one-line note | which status that maps to, whether the move is allowed, whether to ping the teacher |
| the wording of a reminder | whether a reminder is due at all, to whom, quiet hours, duplicates |
| the morning briefing (`/summary`, "who is at risk this week?") — written from fact lines | every flag in those fact lines: overdue, blocked, silent, submitted, not linked are computed by code; the model may not invent a student or a reason; if it fails, the plain fact list is sent |

Every model call returns JSON validated by a Pydantic schema with a **confidence** field. Three outcomes only: **CREATE / ASK / FAIL**. Below threshold or missing deadline → the bot **asks one clarifying question and never guesses**. Schema failure → logged as `model_failure`, user gets a plain "please rephrase". A photo or file is a submission **with no model call at all**.

Context injection: the model is told the current IST time, the subject and class (so it never asks "which subject?"), and Indian-English conventions ("day after" = day after tomorrow; "bring it" = 08:00 start of school).

Thresholds (0.7 create / 0.3 "not an assignment" / 0.6 student intent) are product knobs at the top of `brain.py`.

### 4.6 Failure handling

| Failure | What happens | Log event |
|---|---|---|
| Model returns broken/invalid JSON | user asked to rephrase | `model_failure` |
| Model unsure about the deadline | one clarifying question | — |
| Model fails while writing a reminder | template text is sent instead | `reminder_sent` |
| Telegram network error during polling | wait 5 s, retry; offset preserved | `[NETWORK]` line |
| One update crashes a handler | logged, skipped; bot keeps serving others | `handler_error` |
| Same Telegram update delivered twice | ignored | `duplicate_update_ignored` |
| Bot restarted between preview and Confirm | "This preview has expired, please send again" | — |
| Reminder due during quiet hours | deferred to the next run after 07:00 | `reminder_deferred` |
| Mini App opened outside Telegram / forged signature | 401 | — |

### 4.7 Observable logs

`db.log_event()` writes to the `events` table **and** prints one line, so the terminal is the audit trail during the demo and the Mini App's "Recent activity" reads the same table. Required events and where they fire:

`assignment_created` · `student_notified` · `status_changed` · `student_update` · `submission_received` · `feedback_sent` · `reminder_sent` · `reminder_deferred` · `deadline_changed` · `model_failure` · `handler_error` · `duplicate_update_ignored` · `user_linked`

---

## 5. Tests

```bash
python tests.py
```

Three groups, 14 cases, no network or key needed, throw-away DB:
1. **Reminder rules** — blocked nudges the teacher not the student; silence threshold; overdue precedence; done students are never reminded; quiet hours boundaries.
2. **Attention Board + state rules** — ranking, "did not see it" vs "did not do it", no backwards moves after submission.
3. **Trust boundaries** — forged `initData` rejected and signed accepted; invite code cannot be reused or guessed; duplicate update ignored; a deadline change resets exactly the right rows.

**Chat-flow test (end to end, no phone):**

```bash
python flow_test.py
```

Feeds 30 fake Telegram updates straight into `handle_update` — the whole scenario plus attack cases (stranger before linking, reused invite code, malformed button data, a non-author tapping Confirm, double taps, replayed `update_id`, acknowledging after submission, deadline in the past) — and checks each reply. It exercises the real model, so it needs `ANTHROPIC_API_KEY`; this is also the NL-interpretation eval. Writing it found four real bugs before the demo: a non-author's tap consumed the teacher's preview (author check now runs before the pop), malformed callback data raised, a teacher's sticker triggered a model call on empty text, and an unreachable model API crashed the handler instead of logging `model_failure` and telling the user to retry.

`python brain.py` additionally runs 9 messy messages from the pilot pack through the parsers and prints CREATE/ASK/IGNORE and intents.

### Manual live scenario (the brief's section 9)

1. Reset the DB. Priya is linked; Aarav links with `S-8A-AARAV`; Diya stays unlinked.
2. Priya: *"Do exercise 4.2 q1 to q8, bring it day after"* → preview → Confirm. Aarav receives it; Diya shows as "not linked" on the board. Then *"move it to Friday evening"* → change preview → Confirm → Aarav notified, `deadline_changed` logged.
3. Aarav: *"done half"* → in progress; *"i dont have the book"* → blocked, teacher pinged. (Second student stays silent.)
4. Priya: `/remind force` → teacher nudge for the blocked student, "did you see it?" for the silent one; run again → everything skipped. Priya: *"who is at risk this week?"* → a model-written briefing from code-computed facts (`summary_sent`).
5. Aarav sends a photo → `submission_received`; Priya opens `/app`, writes feedback, Approve → Aarav gets a Telegram message; board shows Done.
6. Open the Mini App URL in Chrome → "Access denied". Send `S-8A-AARAV` again from another phone → "already used".

---

## 6. How AI was used to build this, and where it was overridden

Built with Claude Code as a pair: the model generated most code; I set the product thesis, made every design decision, and asked for explanations until I could defend each line. Commit history shows the order of decisions.

Places I overrode or redirected the AI:
- **Context instead of questions**: the first parser asked "which subject?" — I pushed for injecting known facts (subject, class) so the bot only asks what the teacher alone knows.
- **Polling vs webhook**: the AI proposed polling as "easier"; I asked for the full trade-off and chose polling for demo reliability with webhook as the documented production path (idempotency table already in place for at-least-once delivery).
- **Correction semantics**: I decided which rows a deadline change resets (acknowledged → assigned; date-based reminders cleared; engaged students untouched).
- **Which reminders are the student's problem vs the teacher's**: blocked students are escalated, not nagged.
- **Tests exposed a real bug** the AI had written: `db.connect()` never closed connections. Fixed at the root, not in the test.

---

## 7. Known limitations and deliberate cuts (honest list)

Cut from the brief's list, with the production version we would build:
- **Creating a class context in-product** (Cap. 1): the school, classes, memberships and invite codes are seeded by `db.py` (the brief allows "seed data or a setup command"). Production: a coordinator flow that creates a class and mints invite codes — the `invites` table already supports it.
- **Targeting one student or a group**: assignments go to the whole class. The data is already per-student (`assignment_students`), so targeting is a picker on the preview, not a model change.
- **Correcting instructions / cancelling**: only deadline changes are built. Same preview-confirm pattern; `cancelled` exists in the schema.
- **Two teachers sharing a class, a student moving 8A→8B**: supported by the membership model (one row per person-class-role), not demonstrated.
- **Coordinator view (Meera)**: not built; a `coordinator` membership at school scope plus a read-only board across classes is a week-4 item.

Product ideas from the build, designed but deferred (see PRD plan): voice-note assignment creation (speech-to-text in front of the same parser), per-student timeline (the `events` table already has it), fairness guardrails for "blocked" (reason required; repeat-blocker pattern on the board; per-student extensions), holidays/leave pausing reminders, Mini App filters, scoring/scorecards (a non-goal for the pilot — the pain is visibility, not marks).

Technical limitations:
- **Pilot simplifications**: a teacher's first class is used; a student's *text* applies to their latest open assignment; corrections apply to the teacher's latest assignment. A *photo or file* with more than one open assignment asks "Which assignment is this for?" with one button per assignment (the picker pattern a production version would extend to text, e.g. via reply-to).
- **Preview state is in memory** (`pending` dict). A bot restart between preview and Confirm expires the preview. Production: persist drafts in SQLite.
- **Quick tunnel URL changes** every restart; `MINI_APP_URL` must be updated and the bot restarted.
- **Polling, single process.** Fine for one school; at 100 schools: webhook + a queue + Postgres, with the idempotency table doing the retry safety.
- **Model cost**: every teacher message with an open assignment makes two model calls (classify, then parse). A keyword pre-filter would cut the first.
- **Coordinator view (Meera)** is not built; the access model supports it (a `coordinator` membership at school scope) — week-4 item in the PRD.
- **Voice notes** are not built (bonus item). The "who is at risk this week?" query is built (`/summary` or ask in plain words).
- No rate limiting on the Mini App API; no migrations (reset only).
