import os
import requests
from dotenv import load_dotenv
import db

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

def send_message(chat_id, text):
    requests.post(
        f"{BASE_URL}/sendMessage",
        json={"chat_id": chat_id, "text": text},
        timeout=10,
    )


# ============ DATABASE HELPERS (who is this person?) ============

def find_user(telegram_id):
    with db.connect() as conn:
        return conn.execute(
            "SELECT * FROM users WHERE telegram_id = ?", (telegram_id,)
        ).fetchone()

def roles_of(user_id):
    with db.connect() as conn:
        return conn.execute("""
            SELECT memberships.role, classes.name AS class_name
            FROM memberships
            JOIN classes ON classes.id = memberships.class_id
            WHERE memberships.user_id = ?
        """, (user_id,)).fetchall()

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
    return f"Linked! You are {user['display_name']}, a {invite['role']} in class {cls['name']}."


# ============ BUSINESS LOGIC ============

def handle_update(update):
    message = update.get("message")
    if message is None:
        return

    chat_id = message["chat"]["id"]
    telegram_id = message["from"]["id"]          # the stable ID - this is what we trust
    text = message.get("text", "").strip()

    user = find_user(telegram_id)                # authentication: do we know this ID?

    # ---- Unknown person: the only thing they can do is link with a code ----
    if user is None:
        if text == "/start":
            send_message(chat_id, "Welcome to Classroom Companion! Please send your invite code (for example S-8A-AARAV).")
        else:
            send_message(chat_id, try_link(telegram_id, text.upper()))
        return

    # ---- Known person ----
    roles = roles_of(user["id"])                 # authorization: what are they in which class?
    role_text = ", ".join(f"{r['role']} in {r['class_name']}" for r in roles)

    if text == "/start":
        send_message(chat_id, f"Hello {user['display_name']}! You are: {role_text}.")
    else:
        send_message(chat_id, f"You said: {text}")   # placeholder - real features come next


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