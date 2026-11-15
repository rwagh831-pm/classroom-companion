from datetime import datetime
from zoneinfo import ZoneInfo
from typing import Optional, Literal

import anthropic
from pydantic import BaseModel, Field, ValidationError

client = anthropic.Anthropic()
SCHOOL_TZ = ZoneInfo("Asia/Kolkata")
CONFIDENCE_THRESHOLD = 0.7
NOT_ASSIGNMENT_THRESHOLD = 0.3
STUDENT_CONFIDENCE_THRESHOLD = 0.6


def get_text(response):
    return "".join(block.text for block in response.content if block.type == "text")


# =====================================================================
# PART 1 — TEACHER: message -> assignment draft
# =====================================================================

class AssignmentDraft(BaseModel):
    title: str = Field(min_length=3, max_length=80)
    instructions: str = Field(min_length=3)
    due_at: Optional[datetime] = None
    confidence: float = Field(ge=0, le=1)
    clarifying_question: Optional[str] = None


SYSTEM_PROMPT = """You turn a teacher's informal message into a structured homework assignment.
Reply with ONLY a JSON object, no other text, in this exact shape:
{"title": "<short title, max 8 words, no due date in the title>",
 "instructions": "<what students must do, in clear words>",
 "due_at": "<ISO 8601 datetime with timezone offset, or null if no deadline was given>",
 "confidence": <0.0 to 1.0: how sure you are this is an assignment AND you understood the deadline>,
 "clarifying_question": "<one short question for the teacher if anything is unclear, else null>"}

Context: an Indian school (Hyderabad). Teachers write informal Indian English.
- "day after" means the day after tomorrow.
- "bring it <day>" means it is due at the start of school that day, 08:00.
- If no time of day is given: evening = 18:00, morning = 08:00, otherwise end of day = 20:00.
- You will be given known facts such as the subject and class. Use them; never ask about them.
- Do not invent what a chapter or exercise contains. Keep the teacher's own wording.

Rules:
- Resolve relative deadlines using the current date/time you are given.
- If the deadline is ambiguous or missing, set confidence below 0.7 and write a clarifying_question.
- If the message is not an assignment at all (a question, a chat message), set confidence below 0.3.
- Never invent a deadline that was not stated or clearly implied.
- Keep every string on one line. Output valid JSON only."""


def parse_assignment(teacher_message, subject=None, class_name=None):
    now = datetime.now(SCHOOL_TZ)

    known = []
    if subject:
        known.append(f"Subject: {subject}")
    if class_name:
        known.append(f"Class: {class_name}")
    context_line = ("Known facts (do not ask about these): " + ", ".join(known) + "\n") if known else ""

    user_prompt = (
        f"Current date and time: {now.strftime('%A %d %B %Y, %H:%M')} (Asia/Kolkata, IST).\n"
        f"{context_line}\n"
        f"Teacher's message:\n{teacher_message}"
    )

    response = client.messages.create(
        model="claude-haiku-5-5",
        max_tokens=1500,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_prompt}],
    )
    raw = get_text(response)

    try:
        draft = AssignmentDraft.model_validate_json(raw)
    except ValidationError as e:
        return {"ok": False, "reason": "model_output_invalid", "detail": str(e), "raw": raw}

    if draft.due_at is not None and draft.due_at.tzinfo is None:
        draft.due_at = draft.due_at.replace(tzinfo=SCHOOL_TZ)

    if draft.confidence < NOT_ASSIGNMENT_THRESHOLD:
        return {"ok": False, "reason": "not_an_assignment"}

    if draft.confidence < CONFIDENCE_THRESHOLD or draft.due_at is None:
        question = draft.clarifying_question or "When is this due?"
        return {"ok": False, "reason": "needs_clarification", "question": question, "draft": draft}

    if draft.due_at <= now:
        return {
            "ok": False,
            "reason": "needs_clarification",
            "question": f"The deadline I understood ({draft.due_at:%a %d %b, %H:%M}) is already in the past. When should it be due?",
            "draft": draft,
        }

    return {"ok": True, "draft": draft}


# =====================================================================
# PART 2 — STUDENT: message -> intent
# =====================================================================

class StudentUpdate(BaseModel):
    intent: Literal["acknowledge", "progress", "blocked", "needs_help", "submit_text", "done_cannot_send", "other"]
    confidence: float = Field(ge=0, le=1)
    note: str = Field(min_length=1, max_length=200)   # one line for the teacher


STUDENT_PROMPT = """You read a student's informal message about a homework assignment and classify the intent.
Reply with ONLY a JSON object, no other text, in this exact shape:
{"intent": "<one of: acknowledge | progress | blocked | needs_help | submit_text | done_cannot_send | other>",
 "confidence": <0.0 to 1.0>,
 "note": "<one short line a teacher can read, e.g. 'Stuck on question 5 - the formula'>"}

Meaning of each intent:
- acknowledge: the student has seen/understood the assignment ("ok", "got it", "will do").
- progress: partly done, working on it ("done half", "doing it now").
- blocked: cannot continue because something is missing or wrong ("no book", "don't have the sheet", "can't do this").
- needs_help: asking a question or confused about the content ("stuck on q5", "which page", "not understanding").
- submit_text: the message ITSELF contains the answer or work.
- done_cannot_send: claims it is finished but cannot send proof ("did it but phone has no space").
- other: unrelated chat or unclear.

Students are 13-14, write informal Indian English, may use "mam"/"sir". If unsure between two intents, pick the one most useful for the teacher to know and lower the confidence. Keep every string on one line. Output valid JSON only."""


def interpret_student_message(student_message, assignment_title):
    user_prompt = f"Assignment: {assignment_title}\n\nStudent's message:\n{student_message}"

    response = client.messages.create(
        model="claude-haiku-5-5",
        max_tokens=1000,
        system=STUDENT_PROMPT,
        messages=[{"role": "user", "content": user_prompt}],
    )
    raw = get_text(response)

    try:
        update = StudentUpdate.model_validate_json(raw)
    except ValidationError as e:
        return {"ok": False, "reason": "model_output_invalid", "detail": str(e), "raw": raw}

    if update.confidence < STUDENT_CONFIDENCE_THRESHOLD or update.intent == "other":
        return {"ok": False, "reason": "unclear", "update": update}

    return {"ok": True, "update": update}


# =====================================================================
# Quick test harness
# =====================================================================

# =====================================================================
# PART 3 — REMINDERS: rules choose the kind, the model writes the words
# =====================================================================

class ReminderText(BaseModel):
    text: str = Field(min_length=5, max_length=280)


REMINDER_PROMPT = """You write one short, warm reminder message for a 13-14 year old student, in simple English.
Reply with ONLY a JSON object: {"text": "<the message, max 2 sentences, no emojis>"}
Kinds:
- not_seen: they have not acknowledged the assignment; ask them to reply "ok" if they have seen it.
- due_soon: the deadline is within a day; encourage them to send their work.
- overdue: the deadline has passed; be kind, not scolding; ask them to send it or say what is blocking them.
Always mention the assignment title and the due date/time you are given. Keep every string on one line."""

FALLBACK_REMINDER = {
    "not_seen": "Hi {name}, did you see the assignment '{title}'? It is due {due}. Reply 'ok' if you have seen it.",
    "due_soon": "Hi {name}, '{title}' is due {due}. Please send your work when it is ready.",
    "overdue":  "Hi {name}, '{title}' was due {due}. Please send it, or tell me what is stopping you.",
}


def write_reminder(kind, student_name, title, due_text):
    prompt = f"Kind: {kind}\nStudent: {student_name}\nAssignment: {title}\nDue: {due_text}"
    try:
        response = client.messages.create(
            model="claude-haiku-5-5", max_tokens=600, system=REMINDER_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )
        return ReminderText.model_validate_json(get_text(response)).text
    except Exception:
        return FALLBACK_REMINDER[kind].format(name=student_name, title=title, due=due_text)

if __name__ == "__main__":
    print("=== TEACHER MESSAGES ===")
    for msg in [
        "Do exercise 4.2 q1 to q8, bring it day after. Those who did the extra sheet last week can skip q8.",
        "Read chapter 3 and make notes.",
        "which page was it",
    ]:
        print("\nTEACHER:", msg)
        result = parse_assignment(msg, subject="Maths", class_name="8A")
        if result["ok"]:
            d = result["draft"]
            print(f"  CREATE -> {d.title!r} | due {d.due_at:%a %d %b, %H:%M} | confidence {d.confidence}")
        elif result["reason"] == "needs_clarification":
            print(f"  ASK    -> {result['question']}")
        elif result["reason"] == "not_an_assignment":
            print("  IGNORE -> not an assignment")
        else:
            print("  FAIL   ->", result["detail"][:150])

    print("\n=== STUDENT MESSAGES ===")
    for msg in [
        "ok got it mam",
        "done half will finish tonight",
        "stuck on question 5, not understanding the formula",
        "mam i did it but my phone has no space cant send photo",
        "i dont have the textbook, cant do it",
        "lol",
    ]:
        print("\nSTUDENT:", msg)
        result = interpret_student_message(msg, "Exercise 4.2, Q1-Q8")
        if result["ok"]:
            u = result["update"]
            print(f"  {u.intent.upper():<16} confidence {u.confidence} | note: {u.note}")
        elif result["reason"] == "unclear":
            print(f"  UNCLEAR          (model said {result['update'].intent}, {result['update'].confidence})")
        else:
            print("  FAIL ->", result["detail"][:150])