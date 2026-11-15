import os
import hmac
import hashlib
import json
from datetime import datetime
from zoneinfo import ZoneInfo
from urllib.parse import parse_qsl

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import HTMLResponse
from dotenv import load_dotenv

import db

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
SCHOOL_TZ = ZoneInfo("Asia/Kolkata")

app = FastAPI()


# ============ TRUST BOUNDARY ============

def verify_init_data(init_data):
    pairs = dict(parse_qsl(init_data, keep_blank_values=True))
    received_hash = pairs.pop("hash", None)
    if received_hash is None:
        return None
    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(pairs.items()))
    secret_key = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    expected_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected_hash, received_hash):
        return None
    return json.loads(pairs["user"])

def current_user(init_data):
    tg_user = verify_init_data(init_data)
    if tg_user is None:
        raise HTTPException(status_code=401, detail="Invalid Telegram signature")
    with db.connect() as conn:
        user = conn.execute("SELECT * FROM users WHERE telegram_id = ?", (tg_user["id"],)).fetchone()
    if user is None:
        raise HTTPException(status_code=403, detail="Not linked yet. Send your invite code to the bot first.")
    return user


# ============ RULES: the Attention Board ============

def attention_level(status, telegram_id, due_at, now):
    """Pure rules. Returns (level, reason). Higher level = needs the teacher sooner."""
    if telegram_id is None:
        return 2, "Not linked yet - cannot see assignments"
    if status in ("submitted",):
        return 1, "Submitted - waiting for your review"
    if status in ("completed", "cancelled"):
        return 0, "Done"
    overdue = now > due_at
    if status == "blocked":
        return 3, "Blocked - needs your help"
    if overdue and status == "assigned":
        return 3, "Overdue and never acknowledged - did not see it?"
    if overdue:
        return 3, "Overdue - saw it but has not submitted"
    if status == "assigned":
        return 2, "Has not acknowledged yet - did not see it?"
    return 0, "On track"


def fmt(dt_text):
    return datetime.fromisoformat(dt_text).strftime("%a %d %b, %H:%M")


# ============ API ============

@app.get("/api/me")
def me(x_init_data: str = Header(default="")):
    user = current_user(x_init_data)
    with db.connect() as conn:
        teacher_rows = conn.execute(
            "SELECT 1 FROM memberships WHERE user_id = ? AND role = 'teacher'", (user["id"],)).fetchone()
    return {"name": user["display_name"], "view": "teacher" if teacher_rows else "student"}


@app.get("/api/teacher")
def teacher_dashboard(x_init_data: str = Header(default="")):
    user = current_user(x_init_data)
    now = datetime.now(SCHOOL_TZ)
    with db.connect() as conn:
        classes = conn.execute("""
            SELECT classes.id, classes.name, memberships.subject
            FROM memberships JOIN classes ON classes.id = memberships.class_id
            WHERE memberships.user_id = ? AND memberships.role = 'teacher'
        """, (user["id"],)).fetchall()
        if not classes:
            raise HTTPException(status_code=403, detail="You are not a teacher in any class.")

        # AUTHORIZATION: everything below is filtered to THIS teacher's class ids.
        class_ids = [c["id"] for c in classes]
        placeholders = ",".join("?" * len(class_ids))

        rows = conn.execute(f"""
            SELECT a.id AS assignment_id, a.title, a.subject, a.due_at, a.class_id,
                   u.id AS student_id, u.display_name, u.telegram_id,
                   s.status, s.updated_at
            FROM assignment_students s
            JOIN assignments a ON a.id = s.assignment_id
            JOIN users u ON u.id = s.student_id
            WHERE a.class_id IN ({placeholders})
            ORDER BY a.created_at DESC, u.display_name
        """, class_ids).fetchall()

        notes = {}
        for e in conn.execute("""
            SELECT actor_id, assignment_id, detail FROM events
            WHERE type IN ('student_update', 'status_changed') ORDER BY id
        """).fetchall():
            notes[(e["actor_id"], e["assignment_id"])] = e["detail"]

        recent = conn.execute(f"""
            SELECT e.type, e.detail, e.created_at, u.display_name, a.title
            FROM events e
            LEFT JOIN users u ON u.id = e.actor_id
            LEFT JOIN assignments a ON a.id = e.assignment_id
            WHERE a.class_id IN ({placeholders}) OR e.assignment_id IS NULL
            ORDER BY e.id DESC LIMIT 12
        """, class_ids).fetchall()

    attention, assignments = [], {}
    for r in rows:
        due = datetime.fromisoformat(r["due_at"])
        level, reason = attention_level(r["status"], r["telegram_id"], due, now)
        item = {
            "student": r["display_name"], "assignment": r["title"], "status": r["status"],
            "level": level, "reason": reason, "note": notes.get((r["student_id"], r["assignment_id"])),
            "due": fmt(r["due_at"]),
        }
        if level >= 1:
            attention.append(item)
        a = assignments.setdefault(r["assignment_id"], {
            "title": r["title"], "subject": r["subject"], "due": fmt(r["due_at"]), "students": []})
        a["students"].append({"name": r["display_name"], "status": r["status"], "level": level})

    attention.sort(key=lambda x: -x["level"])
    return {
        "name": user["display_name"],
        "classes": [dict(c) for c in classes],
        "attention": attention,
        "assignments": list(assignments.values()),
        "recent": [dict(e) for e in recent],
    }


@app.get("/api/student")
def student_dashboard(x_init_data: str = Header(default="")):
    user = current_user(x_init_data)
    with db.connect() as conn:
        # AUTHORIZATION: only rows where student_id = me.
        rows = conn.execute("""
            SELECT a.id, a.title, a.subject, a.instructions, a.due_at, s.status,
                   (SELECT COUNT(*) FROM submissions sub WHERE sub.assignment_id = a.id AND sub.student_id = s.student_id) AS submissions
            FROM assignment_students s JOIN assignments a ON a.id = s.assignment_id
            WHERE s.student_id = ?
            ORDER BY a.due_at
        """, (user["id"],)).fetchall()
    return {"name": user["display_name"],
            "assignments": [dict(r, due=fmt(r["due_at"])) for r in rows]}


# ============ THE PAGE ============

PAGE = """<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Classroom Companion</title>
  <script src="https://telegram.org/js/telegram-web-app.js"></script>
  <style>
    body { font-family: sans-serif; margin: 14px; background: var(--tg-theme-bg-color,#fff); color: var(--tg-theme-text-color,#000); }
    h2 { margin: 0 0 4px; } h3 { margin: 18px 0 8px; font-size: 15px; } .muted { opacity: .65; font-size: 13px; }
    .card { border: 1px solid rgba(128,128,128,.35); border-radius: 10px; padding: 10px 12px; margin: 8px 0; }
    .lvl3 { border-left: 6px solid #e5484d; } .lvl2 { border-left: 6px solid #f0a020; } .lvl1 { border-left: 6px solid #3b8bd4; } .lvl0 { border-left: 6px solid #3dbb6a; }
    .pill { display: inline-block; padding: 2px 8px; border-radius: 10px; font-size: 12px; background: rgba(128,128,128,.18); margin: 2px 4px 2px 0; }
    .row { display: flex; justify-content: space-between; gap: 8px; }
  </style>
</head>
<body>
  <div id="out">Loading...</div>
  <script>
    const tg = window.Telegram.WebApp; tg.ready(); tg.expand();
    const H = { headers: { 'X-Init-Data': tg.initData } };
    const out = document.getElementById('out');
    const esc = s => String(s ?? '').replace(/[&<>]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));

    async function api(path) {
      const r = await fetch(path, H); const data = await r.json();
      if (!r.ok) throw new Error(data.detail); return data;
    }

    function teacherView(d) {
      let h = `<h2>Hello ${esc(d.name)}</h2><div class="muted">${d.classes.map(c => esc(c.name)+' · '+esc(c.subject)).join(', ')}</div>`;
      h += `<h3>Needs attention now (${d.attention.length})</h3>`;
      if (!d.attention.length) h += `<div class="muted">Everyone is on track. 🎉</div>`;
      for (const a of d.attention) {
        h += `<div class="card lvl${a.level}"><div class="row"><b>${esc(a.student)}</b><span class="pill">${esc(a.status)}</span></div>
              <div>${esc(a.reason)}</div><div class="muted">${esc(a.assignment)} · due ${esc(a.due)}</div>
              ${a.note ? `<div class="muted">“${esc(a.note)}”</div>` : ''}</div>`;
      }
      h += `<h3>Assignments</h3>`;
      for (const a of d.assignments) {
        h += `<div class="card"><div class="row"><b>${esc(a.title)}</b><span class="muted">due ${esc(a.due)}</span></div>
              <div>${a.students.map(s => `<span class="pill">${esc(s.name)}: ${esc(s.status)}</span>`).join('')}</div></div>`;
      }
      h += `<h3>Recent activity</h3>`;
      for (const e of d.recent) {
        h += `<div class="muted">${esc(e.created_at.slice(11,16))} · ${esc(e.display_name||'system')} · ${esc(e.type)} ${e.title ? '· '+esc(e.title) : ''} ${e.detail ? '· '+esc(e.detail) : ''}</div>`;
      }
      return h;
    }

    function studentView(d) {
      let h = `<h2>Hello ${esc(d.name)}</h2><h3>My assignments</h3>`;
      if (!d.assignments.length) h += `<div class="muted">Nothing assigned yet.</div>`;
      for (const a of d.assignments) {
        h += `<div class="card"><div class="row"><b>${esc(a.title)}</b><span class="pill">${esc(a.status)}</span></div>
              <div class="muted">${esc(a.subject)} · due ${esc(a.due)}</div><div>${esc(a.instructions)}</div>
              <div class="muted">Submissions: ${a.submissions}</div></div>`;
      }
      return h;
    }

    (async () => {
      try {
        const me = await api('/api/me');
        const d = await api(me.view === 'teacher' ? '/api/teacher' : '/api/student');
        out.innerHTML = me.view === 'teacher' ? teacherView(d) : studentView(d);
      } catch (e) { out.textContent = 'Access denied: ' + e.message; }
    })();
  </script>
</body>
</html>"""

@app.get("/", response_class=HTMLResponse)
def home():
    return PAGE