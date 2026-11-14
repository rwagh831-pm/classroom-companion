# Classroom Companion — Build Plan (Super Intelli Machines PM Take-Home)

Owner: Rahul Wagh. Submit to: product@superintellimachines.ai
Deadline: Monday (we have today + tomorrow + Monday). Build fast, but learn every piece.

---

## 0. The one-line product thesis (our north star)

**Classroom Companion turns messy classroom chat into one clear signal: "who needs your attention today" — so a teacher never has to read 40 messages again.**

Everything we build feeds that signal. If a feature does not help a teacher or coordinator see *who needs attention*, it is probably scope we cut.

Evidence from Appendix A (the real pain):
- Teacher: "I do not have time to read forty messages. I need to know who to talk to tomorrow morning."
- Teacher: "Half my class never replies. I cannot tell 'did not see it' from 'did not do it'."
- Meera (coordinator): wants to know which classes are falling behind; has no visibility today.

---

## 1. The chat vs Mini App split (worth 20 points — they WILL ask us to defend it)

Rule we will defend: **Chat = act in the moment. App = see the state.**

- **Telegram Bot (chat surface):** fast, informal, where students already are.
  - Student: receive assignment, acknowledge, ask for help, report progress, submit (text/photo/file).
  - Teacher: create an assignment in natural language; get pinged when someone is at risk.
  - System: send reminders.
- **Telegram Mini App (structured surface):** the calm dashboard you scan.
  - Teacher: classes, assignments, statuses, recent activity, and the **"who needs attention"** triage list; send feedback.
  - Student: my assignments, deadlines, status, feedback history.

Why: a message ("I'm stuck on Q5") belongs in chat. A list of 30 students' statuses belongs in an app, not scrolled in chat.

---

## 2. Scope — what we BUILD vs what we CUT (depth beats breadth)

The rubric rewards a small system that fully works. We build a deep vertical slice that makes the Minimum Live Scenario (PDF section 9) work end to end.

### We BUILD (the spine):
1. One school, one grade, two sections (8A, 8B). One teacher, two students (one linked, one not yet).
2. Onboard students via **invite code**.
3. Teacher creates an assignment in **natural language** with a **relative deadline** ("tomorrow evening"). LLM parses it; Pydantic validates it.
4. Student: acknowledge / ask help / report progress / submit (text + photo).
5. State **persists** in SQLite (survives restart).
6. **Reminder job** that behaves differently for *blocked* vs *silent* students; manual trigger for demo; respects quiet hours (9pm–7am IST).
7. Teacher **summary** that highlights risk (not raw chat).
8. Teacher **feedback** after submission → student sees it in bot + Mini App.
9. **Server-side access checks** (validate Telegram initData; a teacher cannot see another class; a student sees only their own).
10. **Idempotency** so Telegram retries/double-taps do not create duplicates.
11. The **"Make One Thing Great"** feature: the **Attention Board** ("who needs attention right now").

### We CUT (and document why — the rubric rewards honest cuts):
- Multiple schools / multi-tenant scaling (out of scope per PDF).
- Full role matrix (coordinator gets a read-only school view only).
- Fancy UI / design system (out of scope per PDF).
- Native mobile, SSO, payments (out of scope per PDF).
- Full assignment lifecycle: we use a **smaller justified state model** (see section 4).

---

## 3. The domain model (SQLite tables) — relationships must be clear

- `schools(id, name, timezone)`
- `classes(id, school_id, name)`  e.g. 8A, 8B
- `users(id, telegram_id, display_name, created_at)`  (telegram_id null until linked)
- `roles(id, user_id, role, class_id)`  role = teacher|student|coordinator; scoped to a class. One person can hold many rows (teacher in 8A, not 8B).
- `invites(code, class_id, role, expires_at, used_by)`  invite codes, single-use.
- `assignments(id, class_id, created_by, title, instructions, due_at, target, status, created_at)`
- `assignment_targets(assignment_id, student_id)`  which students it applies to.
- `submissions(id, assignment_id, student_id, kind, content, created_at)`  kind = text|photo|file.
- `events(id, type, actor_id, assignment_id, payload, created_at)`  the LOG (rubric wants observable logs).
- `idempotency(key, created_at)`  seen Telegram update ids, to drop duplicates.

Access rule enforced on the SERVER for every request: "does this user hold a role in this class?" Never trust the client.

---

## 4. State model (smaller, justified — PDF allows this)

Per-student status on an assignment: **assigned → acknowledged → in_progress → blocked → submitted → completed**, plus **overdue** (derived from time) and **cancelled**.
We drop `draft` and `needs_revision` for the pilot and say so: draft adds a teacher editing flow we do not need to prove the core loop; needs_revision is a second feedback cycle we can add in week 4.

---

## 5. Where the MODEL decides vs where RULES decide (AI judgment — 15 points)

**Model (LLM) decides (language, fuzzy):**
- Turn "Do exercise 4.2 q1–q8, bring it day after" into structured assignment fields.
- Turn "tomorrow evening" / "next Friday" into a concrete date — but see fallback below.
- Read a student message ("stuck on Q5") → intent = needs_help; ("done, sending photo") → progress.
- Write the reminder wording and the teacher risk summary.

**Rules (code) decide (facts, money-like, safety):**
- Who can see/do what (access checks).
- Is a student overdue? (compare due_at to now — never ask the model the time).
- Quiet hours 9pm–7am: code blocks sends, model never overrides.
- Duplicate detection (idempotency keys).
- Final assignment is saved only after Pydantic validation passes.

**Containing bad output (fallback for uncertainty):**
- Every LLM parse returns a `confidence` + the structured data. If confidence is low OR Pydantic fails OR a relative date is ambiguous, the bot does NOT guess — it asks the teacher a one-line clarifying question ("Did you mean Fri 15 Nov or next Fri 22 Nov?"). This is our safety net and a strong interview story.

---

## 6. Smart additions (first-principles extras — cheap, high-impact, great learning)

Chosen because they cost little once the spine exists AND they hit bonus points + the deepest pain:
1. **Attention Board** (the "one thing great"): one screen = students sorted by risk (blocked > overdue > silent > on-track), each with the one action to take. Directly answers "who do I talk to tomorrow morning."
2. **"Who is at risk this week?" natural-language query** (bonus +10): teacher types the question in chat; one LLM call over the structured state returns a short answer. Reuses the Attention Board data.
3. **Language-understanding eval harness** (bonus +10): a small test set of messy real messages (from Appendix A) → expected parse, scored, with a target of zero dangerous mis-parses. (Rahul already built this pattern in the course — Build 8.)
4. **Confidence + clarifying-question fallback** (section 5) — our headline "we contain bad model output" story.
5. **"Did not see it vs did not do it"** distinction, surfaced on the board — solves a pain quote directly. We know this from read-receipts/acknowledge state, not guessing.

These five are our "ownership" differentiators. We cut any of 2/3 if time runs short; 1, 4, 5 are core.

---

## 7. Tech stack (named, and why — all explainable in the interview)

- **Python** (Rahul has it).
- **Telegram Bot API called directly with `requests`** — the bot, using long polling (no public URL needed for the bot). Raw first (we understand every call), framework later if needed. Bot username: @ccGreenfieldBot.
- **FastAPI + uvicorn** — serves the Mini App pages + the API; validates Telegram identity on the server.
- **SQLite (sqlite3, built in)** — persistent storage, survives restart, zero setup, every line readable.
- **anthropic** — the LLM (claude-haiku-5-5 for cheap dev).
- **pydantic** — structured output + validation (Rahul's Build 11 skill).
- **A tunnel (cloudflared or ngrok)** — gives the Mini App an HTTPS URL for the demo (PDF allows a local tunnel).
- **Reminders:** start with a manual `/run_reminders` command (PDF requires a manual trigger), then add a simple scheduler loop; state lives in SQLite so it recovers after restart.

---

## 8. The 3-day schedule (flexible — go faster if we can)

### Day 1 (today) — Foundations + the brain
- Create the Telegram bot (@BotFather), get the token.
- Create the GitHub repo; first real commit; add `.env.example`; keep secrets out of git.
- Project skeleton + SQLite schema + seed data (the pilot: school, 8A/8B, 1 teacher, 2 students, invite codes).
- Minimal bot that replies (prove the pipe works).
- The assignment-creation brain: natural language + relative deadline → Pydantic-validated JSON, with the confidence/clarify fallback.
- Draft the one-page PRD.

### Day 2 (tomorrow) — The loops + the app
- Student flow: link via invite code, acknowledge, ask help, report progress, submit (text/photo).
- Persist everything; write to the events log.
- Teacher summary (risk highlights) via LLM.
- Feedback flow (teacher → student, both surfaces).
- Mini App (FastAPI + simple HTML/JS): teacher view + student view; validate initData server-side (access boundary).
- The Attention Board.

### Day 3 (Monday) — Make it real + ship
- Reminder job: blocked vs silent logic, quiet hours, manual trigger, restart recovery.
- Idempotency for Telegram retries.
- One bonus: the "who is at risk this week?" query and/or the eval harness.
- 3 test cases + observable logs for the five required events.
- README (architecture diagram, access/state/reminder/identity models, LLM boundary, failures, limitations).
- Record the 5–8 min demo video (run the live scenario).
- Final intentional commits; submit the email.

---

## 9. Deliverables checklist (PDF section 10) — do not miss any

- [ ] 1. Deployed/working Telegram Mini App + bot (1 teacher, 2 students). Share handle + link/tunnel.
- [ ] 2. Teacher view + student view inside the Mini App.
- [ ] 3. Persistent storage (SQLite) that survives restart.
- [ ] 4. Background reminder job (manual trigger + restart recovery).
- [ ] 5. GitHub repo: intentional commit history, `.env.example`, setup steps, test accounts, no secrets.
- [ ] 6. One-page PRD (user, problem, success metrics, MVP cut, non-goals, week 1/4/12 plan).
- [ ] 7. README (architecture, runbook, access model, state model, reminder policy, identity model, LLM boundary, failure handling, known limitations).
- [ ] 8. Demo video 5–8 min (assign → update → remind → submit → feedback → Mini App reflects state; + how we used AI and where we overrode it).
- [ ] 9. At least 3 test cases (automated or reproducible steps).
- [ ] 10. Observable logs for: assignment created, reminder sent, submission received, feedback sent, model failure.
- [ ] 11. Short implementation note (<=500 words): assumptions, domain/access model, state model, reminder policy, failures handled, what we'd improve next.

## 10. Minimum Live Scenario we must make work (PDF section 9) — our acceptance test
1. School + class with a teacher and two students; one linked, one not.
2. Teacher assigns work with a relative deadline, then changes the deadline.
3. One student reports progress then says blocked; the other stays silent.
4. Reminder job runs and behaves differently for blocked vs silent.
5. A student submits (text/file/photo); teacher gives feedback; Mini App reflects final state.
6. We attempt one unauthorised/wrong-context action; the system safely rejects it.

## 11. Interview prep (so the build pays off)
- 10 min live demo of the scenario above.
- 15 min product walkthrough: why this design, who it's for, how to measure success.
- 20 min LIVE change we build on the spot (e.g., add a status, change reminder policy, add a second school) — so we must understand our own code.
- 15 min trade-offs: what breaks at 100 schools, how to test the language flows, what's next.
