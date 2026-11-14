import os
import requests
from dotenv import load_dotenv

# STEP 1: Read the secrets from the .env file.
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")

if BOT_TOKEN is None:
    print("ERROR: BOT_TOKEN not found. Is .env in this folder, and are you running from this folder?")
    raise SystemExit

# STEP 2: Build the web address for Telegram's "getMe" method.
url = f"https://api.telegram.org/bot{BOT_TOKEN}/getMe"

# STEP 3: Send the request to Telegram's server and wait (at most 10 seconds) for the reply.
response = requests.get(url, timeout=10)

# STEP 4: Read the reply.
print("Status code:", response.status_code)
data = response.json()
print("Reply from Telegram:", data)

if response.status_code == 200 and data["ok"]:
    print("\nSUCCESS. Your bot is alive. Its username is:", data["result"]["username"])
else:
    print("\nSomething is wrong. Check the token in .env.")