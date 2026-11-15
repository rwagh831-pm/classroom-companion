import os
import hmac
import hashlib
import json
from urllib.parse import parse_qsl

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import HTMLResponse
from dotenv import load_dotenv

import db

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")

app = FastAPI()


# ============ THE TRUST BOUNDARY: verify Telegram's signed identity ============

def verify_init_data(init_data):
    """Returns the Telegram user dict if the signature is genuine, else None."""
    pairs = dict(parse_qsl(init_data, keep_blank_values=True))
    received_hash = pairs.pop("hash", None)
    if received_hash is None:
        return None
    data_check_string = "\n".join(f"{key}={value}" for key, value in sorted(pairs.items()))
    secret_key = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    expected_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected_hash, received_hash):
        return None
    return json.loads(pairs["user"])


def current_user(init_data):
    """Authentication (signature) + lookup in OUR database. Raises if either fails."""
    tg_user = verify_init_data(init_data)
    if tg_user is None:
        raise HTTPException(status_code=401, detail="Invalid Telegram signature")
    with db.connect() as conn:
        user = conn.execute("SELECT * FROM users WHERE telegram_id = ?", (tg_user["id"],)).fetchone()
    if user is None:
        raise HTTPException(status_code=403, detail="Not linked yet. Send your invite code to the bot first.")
    return user


# ============ API ============

@app.get("/api/me")
def me(x_init_data: str = Header(default="")):
    user = current_user(x_init_data)
    with db.connect() as conn:
        roles = conn.execute("""
            SELECT memberships.role, memberships.subject, classes.name AS class_name
            FROM memberships JOIN classes ON classes.id = memberships.class_id
            WHERE memberships.user_id = ?
        """, (user["id"],)).fetchall()
    return {"name": user["display_name"], "roles": [dict(r) for r in roles]}


# ============ THE PAGE ============

PAGE = """<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Classroom Companion</title>
  <script src="https://telegram.org/js/telegram-web-app.js"></script>
  <style>
    body { font-family: sans-serif; margin: 16px;
           background: var(--tg-theme-bg-color, #fff); color: var(--tg-theme-text-color, #000); }
  </style>
</head>
<body>
  <h2>Classroom Companion</h2>
  <div id="out">Checking who you are...</div>
  <script>
    const tg = window.Telegram.WebApp;
    tg.ready();
    fetch('/api/me', { headers: { 'X-Init-Data': tg.initData } })
      .then(r => r.json().then(data => ({ ok: r.ok, data })))
      .then(({ ok, data }) => {
        const out = document.getElementById('out');
        if (!ok) { out.textContent = 'Access denied: ' + data.detail; return; }
        out.innerHTML = '<p>Hello <b>' + data.name + '</b></p><ul>' +
          data.roles.map(r => '<li>' + r.role + (r.subject ? ' (' + r.subject + ')' : '') + ' in ' + r.class_name + '</li>').join('') +
          '</ul>';
      });
  </script>
</body>
</html>"""

@app.get("/", response_class=HTMLResponse)
def home():
    return PAGE