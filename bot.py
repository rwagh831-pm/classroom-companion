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
    response = requests.get(f"{BASE_URL}/getUpdates", params={"offset": offset, "timeout": 30}, timeout=35)
    return response.json()["result"]

def send_message(chat_id, text, buttons=None):
    payload = {"chat_id": chat_id, "text": text}
    if buttons:
        payload["reply_markup"] = {"inline_keyboard": buttons}
    requests.post(f"{BASE_URL}/sendMessage", json=payload, timeout=10)

def edit_message(chat_id, message_id, text):
    requests.post(f"{BASE_URL}/editMessageText", json={"chat_id": chat_id, "message_id": message_id, "text": text}, timeout=10)

def answer_callback(callback_id):
    requests.post(f"{BASE_URL}/answerCallbackQuery", json={"callback_query_id": callback_id}, timeout=10)


# ============ DATABASE HELPERS ============

def now_iso():
    return datetime.now(brain.SCHOOL_TZ).isoformat(timespec="seconds")

def find_user(telegram_id):
    with db.connect() as conn:
        return conn.execute("SELECT * FROM users WHERE telegram_id = ?", (telegram_id,)).fetchone()

def find_user_by_id(user_id):
    with db.connect() as conn:
        return conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()

def roles_of(user_id):
    with db.connect() as conn:
        return conn.execute("""
            SELECT memberships.role, memberships.subject, classes.name AS class_name
            FROM memberships JOIN classes ON classes.id = memberships.class_id
            WHERE memberships.user_id = ?
        """, (user_id,)).fetchall()

def teacher_classes(user_id):
    with db.connect() as conn:
        return conn.execute("""
            SELECT classes.id AS class_id, classes.name AS class_name, memberships.subject
            FROM memberships JOIN classes ON classes.id = memberships.class_id
            WHERE memberships.user_id = ? AND memberships.role = 'teacher'
        """, (user_id,)).fetchall()

def students_in_class(class_id):
    with db.connect() as conn:
        return conn.execute("""
            SELECT users.id, users.display_name, users.telegram_id
            FROM memberships JOIN users ON users.id = memberships.user_id
            WHERE memberships.class_id = ? AND memberships.role = 'student'
        """, (class_id,)).fetchall()

def try_link(telegram_id, code):
    """Returns (reply_text, linked_user_row_or_None)."""
    with db.connect() as conn:
        invite = conn.execute("SELECT * FROM invites WHERE code = ?", (code,)).fetchone()
        if invite is None:
            return "That code is not valid. Please check it and try again.", None
        if invite["used_by_telegram_id"] is not None:
            return "That code has already been used. Please ask your teacher for a new one.", None
        conn.execute("UPDATE users SET telegram_id = ? WHERE id = ?", (telegram_id, invite["for_user_id"]))
        conn.execute("UPDATE invites SET used_by_telegram_id = ? WHERE code = ?", (telegram_id, code))
        user = conn.execute("SELECT * FROM users WHERE id = ?", (invite["for_user_id"],)).fetchone()
        cls = conn.execute("SELECT * FROM classes WHERE id = ?", (invite["class_id"],)).fetchone()
    db.log_event("user_linked", actor_id=user["id"], detail=f"{user['display_name']} as {invite['role']} in {cls['name']}")
    return f"Linked! You are {user['display_name']}, a {invite['role']} in class {cls['name']}.", user

def save_assignment(class_id, teacher_id, subject, draft, students):
    with db.connect() as conn:
        cursor = conn.execute(
            "INSERT INTO assignments (class_id, created_by, subject, title, instructions, due_at, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (class_id, teacher_id, subject, draft.title, draft.instructions, draft.due_at.isoformat(), now_iso()),
        )
        assignment_id = cursor.lastrowid
        for student in students:
            conn.execute("INSERT INTO assignment_students (assignment_id, student_id, updated_at) VALUES (?, ?, ?)",
                         (assignment_id, student["id"], now_iso()))
    return assignment_id

def student_assignments(student_id, open_only=False):
    sql = """
        SELECT assignments.*, assignment_students.status AS my_status
        FROM assignment_students JOIN assignments ON assignments.id = assignment_students.assignment_id
        WHERE assignment_students.student_id = ?
    """
    if open_only:
        sql += " AND assignment_students.status NOT IN ('submitted', 'completed', 'cancelled')"
    sql += " ORDER BY assignments.created_at DESC"
    with db.connect() as conn:
        return conn.execute(sql, (student_id,)).fetchall()

def set_student_status(assignment_id, student_id, new_status):
    with db.connect() as conn:
        conn.execute("UPDATE assignment_students SET status = ?, updated_at = ? WHERE assignment_id = ? AND student_id = ?",
                     (new_status, now_iso(), assignment_id, student_id))

def add_submission(assignment_id, student_id, kind, content):
    with db.connect() as conn:
        conn.execute("INSERT INTO submissions (assignment_id, student_id, kind, content, created_at) VALUES (?, ?, ?, ?, ?)",
                     (assignment_id, student_id, kind, content, now_iso()))


# ============ SHORT-LIVED STATE ============
pending = {}


# ============ MESSAGE FORMATS ============

def format_preview(cls, draft):
    return (f"📋 Preview — {cls['subject']} · {cls['class_name']}\n\n{draft.title}\n{draft.instructions}\n\n"
            f"Due: {draft.due_at:%a %d %b, %H:%M}\nTo: whole class\n\nSend this to the students?")

def format_assignment_for_student(a):
    due = datetime.fromisoformat(a["due_at"])
    return f"📚 {a['subject']} — {a['title']}\n{a['instructions']}\n\nDue: {due:%a %d %b, %H:%M}\nStatus: {a['my_status']}"

def notify_teacher(assignment_id, text):
    with db.connect() as conn:
        a = conn.execute("SELECT created_by FROM assignments WHERE id = ?", (assignment_id,)).fetchone()
    teacher = find_user_by_id(a["created_by"])
    if teacher["telegram_id"] is not None:
        send_message(teacher["telegram_id"], text)


# ============ STUDENT RULES: intent -> status ============

STATUS_FOR_INTENT = {
    "acknowledge":      "acknowledged",
    "progress":         "in_progress",
    "blocked":          "blocked",
    "submit_text":      "submitted",
    "done_cannot_send": "in_progress",
    "needs_help":       None,        # status unchanged; teacher is told
}

REPLY_FOR_INTENT = {
    "acknowledge":      "👍 Noted — I've marked it as seen.",
    "progress":         "💪 Great, marked as in progress. Send a photo or your answer when done.",
    "blocked":          "Got it — I've marked you as blocked and told your teacher.",
    "needs_help":       "I've passed your question to your teacher. Keep going with the parts you can.",
    "submit_text":      "✅ Submitted! Your teacher will review it.",
    "done_cannot_send": "No problem — I've told your teacher you've finished. Send the photo when you can, or type your answers here.",
}

TEACHER_PING_INTENTS = {"blocked", "needs_help", "submit_text", "done_cannot_send"}

def can_move(current, new):
    if new is None:
        return False
    if current in ("submitted", "completed"):     # never move backwards from a submission
        return False
    return current != new


# ============ BUSINESS LOGIC ============

def handle_teacher(message, user, classes):
    chat_id = message["chat"]["id"]
    text = message.get("text", "").strip()
    cls = classes[0]
    result = brain.parse_assignment(text, subject=cls["subject"], class_name=cls["class_name"])

    if result["ok"]:
        draft_id = uuid.uuid4().hex[:8]
        pending[draft_id] = {"teacher": user, "class": cls, "draft": result["draft"]}
        buttons = [[{"text": "✅ Confirm & send", "callback_data": f"confirm:{draft_id}"},
                    {"text": "❌ Cancel", "callback_data": f"cancel:{draft_id}"}]]
        send_message(chat_id, format_preview(cls, result["draft"]), buttons=buttons)
    elif result["reason"] == "needs_clarification":
        send_message(chat_id, f"Quick question: {result['question']}\n\nPlease send the assignment again with that detail.")
    elif result["reason"] == "not_an_assignment":
        send_message(chat_id, "That doesn't look like an assignment. To assign work, just describe it, e.g. 'Do exercise 4.2 q1-q8, bring it day after.'")
    else:
        db.log_event("model_failure", actor_id=user["id"], detail=result["detail"][:200])
        send_message(chat_id, "Sorry, I couldn't understand that. Please try rephrasing.")


def handle_student(message, user):
    chat_id = message["chat"]["id"]
    text = message.get("text", "").strip()

    open_items = student_assignments(user["id"], open_only=True)
    if not open_items:
        send_message(chat_id, "You have no open assignments right now. 🎉")
        return
    a = open_items[0]                                   # pilot: latest open assignment
    assignment_id = a["id"]

    # ---- A photo or file IS a submission. No model needed. ----
    if "photo" in message or "document" in message:
        if "photo" in message:
            kind, content = "photo", message["photo"][-1]["file_id"]       # last = largest size
        else:
            kind, content = "file", message["document"]["file_id"]
        add_submission(assignment_id, user["id"], kind, content)
        set_student_status(assignment_id, user["id"], "submitted")
        db.log_event("submission_received", actor_id=user["id"], assignment_id=assignment_id, detail=kind)
        send_message(chat_id, f"✅ Submitted ({kind}) for: {a['title']}. Your teacher will review it.")
        notify_teacher(assignment_id, f"📥 {user['display_name']} submitted a {kind} for '{a['title']}'.")
        return

    if not text:
        return

    # ---- Text: the model reads the intent, rules decide the effect ----
    result = brain.interpret_student_message(text, a["title"])

    if not result["ok"]:
        if result["reason"] == "model_output_invalid":
            db.log_event("model_failure", actor_id=user["id"], assignment_id=assignment_id, detail=result["detail"][:200])
        send_message(chat_id, f"Sorry, I didn't catch that. About '{a['title']}', are you: done, stuck, or still working? Or send a photo of your work.")
        return

    update = result["update"]
    intent = update.intent
    new_status = STATUS_FOR_INTENT.get(intent)

    if intent == "submit_text":
        add_submission(assignment_id, user["id"], "text", text)
        db.log_event("submission_received", actor_id=user["id"], assignment_id=assignment_id, detail="text")

    if can_move(a["my_status"], new_status):
        set_student_status(assignment_id, user["id"], new_status)
        db.log_event("status_changed", actor_id=user["id"], assignment_id=assignment_id, detail=f"{a['my_status']} -> {new_status}")
    else:
        db.log_event("student_update", actor_id=user["id"], assignment_id=assignment_id, detail=f"{intent}: {update.note}")

    send_message(chat_id, REPLY_FOR_INTENT[intent])

    if intent in TEACHER_PING_INTENTS:
        notify_teacher(assignment_id, f"🔔 {user['display_name']} · '{a['title']}' · {intent.replace('_', ' ')}: {update.note}")


def handle_message(message):
    chat_id = message["chat"]["id"]
    telegram_id = message["from"]["id"]
    text = message.get("text", "").strip()

    user = find_user(telegram_id)

    if user is None:
        if text == "/start":
            send_message(chat_id, "Welcome to Classroom Companion! Please send your invite code (for example S-8A-AARAV).")
            return
        reply, linked_user = try_link(telegram_id, text.upper())
        send_message(chat_id, reply)
        if linked_user is not None:
            for a in student_assignments(linked_user["id"], open_only=True):
                send_message(chat_id, format_assignment_for_student(a))
        return

    if text == "/app":
        url = os.getenv("MINI_APP_URL")
        send_message(chat_id, "Open your Classroom Companion dashboard:",
                     buttons=[[{"text": "📊 Open dashboard", "web_app": {"url": url}}]])
        return

    if text == "/start":
        roles = roles_of(user["id"])
        role_text = ", ".join(f"{r['role']} in {r['class_name']}" for r in roles)
        send_message(chat_id, f"Hello {user['display_name']}! You are: {role_text}.")
        for a in student_assignments(user["id"]):
            send_message(chat_id, format_assignment_for_student(a))
        return

    classes = teacher_classes(user["id"])
    if classes:
        handle_teacher(message, user, classes)
    else:
        handle_student(message, user)


def handle_callback(callback):
    answer_callback(callback["id"])
    chat_id = callback["message"]["chat"]["id"]
    message_id = callback["message"]["message_id"]
    action, draft_id = callback["data"].split(":")

    item = pending.pop(draft_id, None)
    if item is None:
        edit_message(chat_id, message_id, "This preview has expired. Please send the assignment again.")
        return
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
        send_message(student["telegram_id"],
                     f"📚 New {cls['subject']} assignment from {teacher['display_name']}\n\n{draft.title}\n{draft.instructions}\n\nDue: {draft.due_at:%a %d %b, %H:%M}")
        db.log_event("student_notified", actor_id=student["id"], assignment_id=assignment_id)
        sent += 1

    edit_message(chat_id, message_id,
                 f"✅ Sent: {draft.title}\nDue {draft.due_at:%a %d %b, %H:%M}\n"
                 f"Delivered to {sent} student(s). {not_linked} not linked yet (they will see it when they link).")


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