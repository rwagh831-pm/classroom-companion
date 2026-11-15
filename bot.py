import os
import uuid
import requests
from datetime import datetime
from dotenv import load_dotenv

import db
import brain

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
BASE_URL = f"https://api.telegram.org/bot{BOT_TOKEN}"


# ============ TELEGRAM HELPERS ============

def get_updates(offset):
    response = requests.get(
        f"{BASE_URL}/getUpdates",
        params={"offset": offset, "timeout": 30},
        timeout=35,
    )
    return response.json()["result"]

def send_message(chat_id, text, buttons=None):
    payload = {"chat_id": chat_id, "text": text}
    if buttons:
        payload["reply_markup"] = {"inline_keyboard": buttons}
    requests.post(f"{BASE_URL}/sendMessage", json=payload, timeout=10)

def edit_message(chat_id, message_id, text):
    requests.post(
        f"{BASE_URL}/editMessageText",
        json={"chat_id": chat_id, "message_id": message_id, "text": text},
        timeout=10,
    )

def answer_callback(callback_id):
    # Tell Telegram "I received the tap" so the button stops spinning.
    requests.post(f"{BASE_URL}/answerCallbackQuery", json={"callback_query_id": callback_id}, timeout=10)


# ============ DATABASE HELPERS ============

def find_user(telegram_id):
    with db.connect() as conn:
        return conn.execute("SELECT * FROM users WHERE telegram_id = ?", (telegram_id,)).fetchone()

def roles_of(user_id):
    with db.connect() as conn:
        return conn.execute("""
            SELECT memberships.role, memberships.subject, classes.name AS class_name
            FROM memberships
            JOIN classes ON classes.id = memberships.class_id
            WHERE memberships.user_id = ?
        """, (user_id,)).fetchall()

def teacher_classes(user_id):
    with db.connect() as conn:
        return conn.execute("""
            SELECT classes.id AS class_id, classes.name AS class_name, memberships.subject
            FROM memberships
            JOIN classes ON classes.id = memberships.class_id
            WHERE memberships.user_id = ? AND memberships.role = 'teacher'
        """, (user_id,)).fetchall()

def students_in_class(class_id):
    with db.connect() as conn:
        return conn.execute("""
            SELECT users.id, users.display_name, users.telegram_id
            FROM memberships
            JOIN users ON users.id = memberships.user_id
            WHERE memberships.class_id = ? AND memberships.role = 'student'
        """, (class_id,)).fetchall()

def try_link(telegram_id, code):
    with db.connect() as conn:
        invite = conn.execute("SELECT * FROM invites WHERE code = ?", (code,)).fetchone()
        if invite is None:
            return "That code is not valid. Please check it and try again."
        if invite["used_by_telegram_id"] is not None:
            return "That code has already been used. Please ask your teacher for a new one."
        conn.execute("UPDATE users SET telegram_id = ? WHERE id = ?", (telegram_id, invite["for_user_id"]))
        conn.execute("UPDATE invites SET used_by_telegram_id = ? WHERE code = ?", (telegram_id, code))
        user = conn.execute("SELECT * FROM users WHERE id = ?", (invite["for_user_id"],)).fetchone()
        cls = conn.execute("SELECT * FROM classes WHERE id = ?", (invite["class_id"],)).fetchone()
    db.log_event("user_linked", actor_id=user["id"], detail=f"{user['display_name']} as {invite['role']} in {cls['name']}")
    return f"Linked! You are {user['display_name']}, a {invite['role']} in class {cls['name']}."

def save_assignment(class_id, teacher_id, subject, draft, students):
    now = datetime.now(brain.SCHOOL_TZ).isoformat(timespec="seconds")
    with db.connect() as conn:
        cursor = conn.execute(
            "INSERT INTO assignments (class_id, created_by, subject, title, instructions, due_at, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (class_id, teacher_id, subject, draft.title, draft.instructions, draft.due_at.isoformat(), now),
        )
        assignment_id = cursor.lastrowid
        for student in students:
            conn.execute(
                "INSERT INTO assignment_students (assignment_id, student_id, updated_at) VALUES (?, ?, ?)",
                (assignment_id, student["id"], now),
            )
    return assignment_id


# ============ SHORT-LIVED STATE: previews waiting for a tap ============
# Kept in memory on purpose. If the bot restarts, an unconfirmed preview is lost (the teacher
# simply sends it again). Confirmed assignments live in the database, so they survive restarts.
pending = {}


def format_preview(cls, draft):
    return (
        f"📋 Preview — {cls['subject']} · {cls['class_name']}\n\n"
        f"{draft.title}\n{draft.instructions}\n\n"
        f"Due: {draft.due_at:%a %d %b, %H:%M}\n"
        f"To: whole class\n\n"
        f"Send this to the students?"
    )

def format_student_notice(teacher_name, subject, draft):
    return (
        f"📚 New {subject} assignment from {teacher_name}\n\n"
        f"{draft.title}\n{draft.instructions}\n\n"
        f"Due: {draft.due_at:%a %d %b, %H:%M}"
    )


# ============ BUSINESS LOGIC ============

def handle_message(message):
    chat_id = message["chat"]["id"]
    telegram_id = message["from"]["id"]
    text = message.get("text", "").strip()

    user = find_user(telegram_id)                       # authentication

    # ---- Unknown person: can only link ----
    if user is None:
        if text == "/start":
            send_message(chat_id, "Welcome to Classroom Companion! Please send your invite code (for example S-8A-AARAV).")
        else:
            send_message(chat_id, try_link(telegram_id, text.upper()))
        return

    # ---- Known person ----
    if text == "/start":
        roles = roles_of(user["id"])
        role_text = ", ".join(f"{r['role']} in {r['class_name']}" for r in roles)
        send_message(chat_id, f"Hello {user['display_name']}! You are: {role_text}.")
        return

    classes = teacher_classes(user["id"])               # authorization: is this person a teacher anywhere?

    if classes:
        # ---- TEACHER: any free text is a possible assignment ----
        cls = classes[0]                                # pilot simplification: first class (class picker comes with the Mini App)
        result = brain.parse_assignment(text, subject=cls["subject"], class_name=cls["class_name"])

        if result["ok"]:
            draft_id = uuid.uuid4().hex[:8]
            pending[draft_id] = {"teacher": user, "class": cls, "draft": result["draft"]}
            buttons = [[
                {"text": "✅ Confirm & send", "callback_data": f"confirm:{draft_id}"},
                {"text": "❌ Cancel",         "callback_data": f"cancel:{draft_id}"},
            ]]
            send_message(chat_id, format_preview(cls, result["draft"]), buttons=buttons)

        elif result["reason"] == "needs_clarification":
            send_message(chat_id, f"Quick question: {result['question']}\n\nPlease send the assignment again with that detail.")

        elif result["reason"] == "not_an_assignment":
            send_message(chat_id, "That doesn't look like an assignment. To assign work, just describe it, e.g. 'Do exercise 4.2 q1-q8, bring it day after.'")

        else:
            db.log_event("model_failure", actor_id=user["id"], detail=result["detail"][:200])
            send_message(chat_id, "Sorry, I couldn't understand that. Please try rephrasing.")
        return

    # ---- STUDENT: placeholder until the next step ----
    send_message(chat_id, f"You said: {text}")


def handle_callback(callback):
    answer_callback(callback["id"])
    chat_id = callback["message"]["chat"]["id"]
    message_id = callback["message"]["message_id"]
    action, draft_id = callback["data"].split(":")

    item = pending.pop(draft_id, None)
    if item is None:
        edit_message(chat_id, message_id, "This preview has expired. Please send the assignment again.")
        return

    # Only the teacher who created the preview may act on it.
    if callback["from"]["id"] != item["teacher"]["telegram_id"]:
        return

    if action == "cancel":
        edit_message(chat_id, message_id, "Cancelled. Nothing was sent.")
        return

    teacher, cls, draft = item["teacher"], item["class"], item["draft"]
    students = students_in_class(cls["class_id"])
    assignment_id = save_assignment(cls["class_id"], teacher["id"], cls["subject"], draft, students)
    db.log_event("assignment_created", actor_id=teacher["id"], assignment_id=assignment_id, detail=draft.title)

    sent, not_linked = 0, 0
    for student in students:
        if student["telegram_id"] is None:
            not_linked += 1
            continue
        send_message(student["telegram_id"], format_student_notice(teacher["display_name"], cls["subject"], draft))
        db.log_event("student_notified", actor_id=student["id"], assignment_id=assignment_id)
        sent += 1

    edit_message(
        chat_id, message_id,
        f"✅ Sent: {draft.title}\nDue {draft.due_at:%a %d %b, %H:%M}\n"
        f"Delivered to {sent} student(s). {not_linked} not linked yet (they will see it when they link)."
    )


def handle_update(update):
    if "message" in update:
        handle_message(update["message"])
    elif "callback_query" in update:
        handle_callback(update["callback_query"])


# ============ DELIVERY (polling) ============

if __name__ == "__main__":
    db.init_db()
    db.seed()
    print("Bot is running (polling). Press Ctrl+C to stop.")
    offset = None
    while True:
        updates = get_updates(offset)
        for update in updates:
            offset = update["update_id"] + 1
            handle_update(update)