import os
import requests
from dotenv import load_dotenv

load_dotenv()
BASE_URL = f"https://api.telegram.org/bot{os.getenv('BOT_TOKEN')}"

def send_message(chat_id, text, buttons=None):
    payload = {"chat_id": chat_id, "text": text}
    if buttons:
        payload["reply_markup"] = {"inline_keyboard": buttons}
    requests.post(f"{BASE_URL}/sendMessage", json=payload, timeout=10)