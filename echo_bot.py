import os
import requests
from dotenv import load_dotenv

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
BASE_URL = f"https://api.telegram.org/bot{BOT_TOKEN}"


# ============ TELEGRAM HELPERS (talking to Telegram's API) ============

def get_updates(offset):
    # Ask Telegram: "any new messages after this offset?" Telegram waits up to 30 s for one.
    response = requests.get(
        f"{BASE_URL}/getUpdates",
        params={"offset": offset, "timeout": 30},
        timeout=35,   # our own safety limit: a bit longer than Telegram's 30 s wait
    )
    return response.json()["result"]

def send_message(chat_id, text):
    requests.post(
        f"{BASE_URL}/sendMessage",
        json={"chat_id": chat_id, "text": text},
        timeout=10,
    )


# ============ BUSINESS LOGIC (what we DO with a message) ============
# This function does not care HOW the update arrived (polling today, webhook on Day 2).

def handle_update(update):
    message = update.get("message")
    if message is None:
        return                                # not a normal message (e.g. an edit) - ignore it

    chat_id = message["chat"]["id"]
    name = message["from"]["first_name"]
    text = message.get("text", "")            # photos have no text; default to "" instead of crashing

    print(f"{name} said: {text}")

    if text == "/start":
        send_message(chat_id, f"Hello {name}! I am Classroom Companion. Say anything and I will echo it back.")
    else:
        send_message(chat_id, f"You said: {text}")


# ============ DELIVERY (polling loop) - this is the ONLY part that changes on Day 2 ============

print("Bot is running (polling). Send it a message in Telegram. Press Ctrl+C here to stop.")
offset = None
while True:
    updates = get_updates(offset)
    for update in updates:
        offset = update["update_id"] + 1      # mark this update as handled; ask for the next ones
        handle_update(update)