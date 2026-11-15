from datetime import datetime
from zoneinfo import ZoneInfo
from typing import Optional

import anthropic
from pydantic import BaseModel, Field, ValidationError

client = anthropic.Anthropic()
SCHOOL_TZ = ZoneInfo("Asia/Kolkata")
CONFIDENCE_THRESHOLD = 0.7         # below this -> ask a clarifying question
NOT_ASSIGNMENT_THRESHOLD = 0.3     # below this -> it is not an assignment at all


# ---------- THE SCHEMA: what a valid draft must look like ----------
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


def get_text(response):
    return "".join(block.text for block in response.content if block.type == "text")


def parse_assignment(teacher_message, subject=None, class_name=None):
    # RULE: code supplies the current date/time and the known facts. The model never guesses them.
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

    # MODEL: understand the language and propose a draft.
    response = client.messages.create(
        model="claude-haiku-5-5",
        max_tokens=1500,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_prompt}],
    )
    raw = get_text(response)

    # RULE 1: must match the schema, else it is a model failure.
    try:
        draft = AssignmentDraft.model_validate_json(raw)
    except ValidationError as e:
        return {"ok": False, "reason": "model_output_invalid", "detail": str(e), "raw": raw}

    # RULE 2: a date without a timezone is school time.
    if draft.due_at is not None and draft.due_at.tzinfo is None:
        draft.due_at = draft.due_at.replace(tzinfo=SCHOOL_TZ)

    # RULE 3: very low confidence -> not an assignment. Do not ask about deadlines.
    if draft.confidence < NOT_ASSIGNMENT_THRESHOLD:
        return {"ok": False, "reason": "not_an_assignment"}

    # RULE 4: unsure or no deadline -> ask, don't create.
    if draft.confidence < CONFIDENCE_THRESHOLD or draft.due_at is None:
        question = draft.clarifying_question or "When is this due?"
        return {"ok": False, "reason": "needs_clarification", "question": question, "draft": draft}

    # RULE 5: a past deadline is never acceptable.
    if draft.due_at <= now:
        return {
            "ok": False,
            "reason": "needs_clarification",
            "question": f"The deadline I understood ({draft.due_at:%a %d %b, %H:%M}) is already in the past. When should it be due?",
            "draft": draft,
        }

    return {"ok": True, "draft": draft}


# ---------- Quick test harness: messy real messages from Appendix A ----------
if __name__ == "__main__":
    tests = [
        "Do exercise 4.2 q1 to q8, bring it day after. Those who did the extra sheet last week can skip q8.",
        "Lab report is due tomorrow evening.",
        "Change of plan, lab report is due Monday not Friday. Sorry! Ignore what I said earlier about the diagram.",
        "Read chapter 3 and make notes.",
        "which page was it",
    ]
    for msg in tests:
        print("\nTEACHER:", msg)
        result = parse_assignment(msg, subject="Maths", class_name="8A")
        if result["ok"]:
            d = result["draft"]
            print(f"  CREATE -> {d.title!r} | due {d.due_at:%a %d %b, %H:%M} | confidence {d.confidence}")
        elif result["reason"] == "needs_clarification":
            print(f"  ASK    -> {result['question']}")
        elif result["reason"] == "not_an_assignment":
            print("  IGNORE -> that doesn't look like an assignment")
        else:
            print("  FAIL   -> model output invalid:", result["detail"][:150])