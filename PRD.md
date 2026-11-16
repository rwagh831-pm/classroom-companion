# PRD — Classroom Companion (pilot)

**Owner:** Rahul Wagh · **Status:** pilot built · **Surface:** Telegram bot + Mini App

## User and problem

**Primary user:** a school teacher (Priya, Maths, class 8A, ~35 students) who assigns homework through chat because that is where students are.
**Secondary:** students (13–14) on a parent's phone; a coordinator (Meera) who wants to know which classes are falling behind.

**The problem is not assigning work — WhatsApp does that.** The problem is the morning after: *"I do not have time to read forty messages. I need to know who to talk to tomorrow morning."* and *"I cannot tell 'did not see it' from 'did not do it'."* The teacher's attention is the scarce resource; chat burns it.

## The job we do

> Turn messy classroom chat into one signal — **who needs my attention today, and why** — without leaving the chat students already use.

**One thing made great:** the **Attention Board** — students ranked blocked › overdue › silent › needs review › on track, each with the reason and the one action. Everything else exists to feed it truthfully.

## Success metrics (pilot, 1 class, 4 weeks)

| Metric | Why it matters | Target |
|---|---|---|
| Acknowledgement rate within 12 h of assignment | proves "did not see it" is now measurable and shrinking | ≥ 80 % |
| Teacher time from "open app" to "know who to talk to" | the core promise | < 60 s, self-reported daily |
| Blocked students the teacher learned about **before** the deadline | the escalation path works | 100 % of blocked reports reach the teacher same day |
| Submissions received through the bot vs outside it | the loop closes in one place | ≥ 70 % |
| Teacher clarifying questions per 10 assignments | model + rules contain bad parses without annoying her | ≤ 2, zero wrong deadlines saved |

Counter-metric: reminders per student per week ≤ 3; zero messages during 21:00–07:00.

## MVP cut (what the pilot does)

- Students onboard by invite code; identity = Telegram id, never the display name.
- Teacher assigns in plain language with a relative deadline; preview → confirm; can change the deadline the same way.
- Students acknowledge, report progress, say they are blocked, ask for help, submit text/photo/file — all in chat, read by the model, enforced by rules.
- Reminder job: blocked → escalate to teacher; silent → "did you see it?"; due-soon and overdue nudges; quiet hours; never twice.
- Mini App: teacher view (Attention Board, assignments, activity, feedback) and student view (my work, deadlines, feedback), with server-side access checks.
- Every meaningful event logged; 14 automated tests on the rules.

## Non-goals (and why)

- Multi-school, SSO, payments, native apps, polished design — out of scope per brief.
- Grading, rubrics, marks — the pain is visibility, not assessment.
- Parent accounts — the student's phone *is* often the parent's; one identity per student keeps the model simple.
- Replacing the teacher's judgement with the model's — the model only reads language; every decision with consequences is a rule.

## Plan

**Week 1 (pilot hardening):** persist previews; "which assignment?" disambiguation (reply-to); "who is at risk this week?" chat query over the board data; named tunnel / hosted URL.
**Week 4 (second class + coordinator):** coordinator read-only school view; teacher sees all her classes; webhook delivery with the idempotency table; migrations instead of resets; language-flow eval set from real messages with a zero-wrong-deadline gate.
**Week 12 (10 schools):** Postgres + job queue; per-school quiet hours and language; voice-note transcription; reminder policy tunable per teacher; on-prem or open-weight model option behind the same `brain.py` boundary.

## Risks

- Students ignore the bot like they ignore the group → mitigated by making acknowledgement one word ("ok") and escalating silence to a human, not more bot messages.
- Model mis-reads a deadline → saved only above a confidence threshold, with a preview the teacher confirms; wrong deadline is the one error we design to zero.
- Teacher trust in "who needs attention" → every board row shows its reason and the underlying event, never a bare score.
