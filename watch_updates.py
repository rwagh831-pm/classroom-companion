import os
import requests
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()
BASE_URL = f"https://api.telegram.org/bot{os.getenv('BOT_TOKEN')}"

def now():
    return datetime.now().strftime("%H:%M:%S")

offset = None
call_number = 0

while True:
    call_number += 1
    print(f"\n[{now()}] call #{call_number}: asking getUpdates with offset={offset}  (Telegram may hold up to 30 s)")

    response = requests.get(
        f"{BASE_URL}/getUpdates",
        params={"offset": offset, "timeout": 30},
        timeout=35,
    )
    updates = response.json()["result"]

    print(f"[{now()}] Telegram answered with {len(updates)} update(s)")
    for update in updates:
        text = update.get("message", {}).get("text")
        print(f"    update_id={update['update_id']}   text={text!r}")
        offset = update["update_id"] + 1
        print(f"    -> offset is now {offset}  (I will ask from here next time)")