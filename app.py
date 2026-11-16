import os
import hmac
import hashlib
import json
from datetime import datetime
from zoneinfo import ZoneInfo
from urllib.parse import parse_qsl
from typing import Literal

import requests
from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from dotenv import load_dotenv

import db
import reminders

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
BASE_URL = f"https://api.telegram.org/bot{BOT_TOKEN}"
SCHOOL_TZ = ZoneInfo("Asia/Kolkata")

app = FastAPI()


def send_telegram(chat_id, text):
    requests.post(f"{BASE_URL}/sendMessage", json={"chat_id": chat_id, "text": text}, timeout=10)


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

def teacher_class_ids(conn, user_id):
    rows = conn.execute("SELECT class_id FROM memberships WHERE user_id = ? AND role = 'teacher'", (user_id,)).fetchall()
    return [r["class_id"] for r in rows]


# ============ RULES: the Attention Board ============

def attention_level(status, telegram_id, due_at, now):
    if status in ("completed", "cancelled"):
        return 0, "Done"
    if telegram_id is None:
        return 2, "Not linked yet - cannot see assignments"
    if status == "submitted":
        return 1, "Submitted - waiting for your review"
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

def now_iso():
    return datetime.now(SCHOOL_TZ).isoformat(timespec="seconds")


# ============ API: who am I ============

@app.get("/api/me")
def me(x_init_data: str = Header(default="")):
    user = current_user(x_init_data)
    with db.connect() as conn:
        is_teacher = bool(teacher_class_ids(conn, user["id"]))
    return {"name": user["display_name"], "view": "teacher" if is_teacher else "student"}


# ============ API: teacher dashboard ============

@app.get("/api/teacher")
def teacher_dashboard(x_init_data: str = Header(default="")):
    user = current_user(x_init_data)
    now = datetime.now(SCHOOL_TZ)
    with db.connect() as conn:
        class_ids = teacher_class_ids(conn, user["id"])
        if not class_ids:
            raise HTTPException(status_code=403, detail="You are not a teacher in any class.")
        ph = ",".join("?" * len(class_ids))

        classes = conn.execute(f"""
            SELECT classes.id, classes.name, memberships.subject
            FROM memberships JOIN classes ON classes.id = memberships.class_id
            WHERE memberships.user_id = ? AND memberships.role = 'teacher'
        """, (user["id"],)).fetchall()

        rows = conn.execute(f"""
            SELECT a.id AS assignment_id, a.title, a.subject, a.due_at,
                   u.id AS student_id, u.display_name, u.telegram_id,
                   s.status
            FROM assignment_students s
            JOIN assignments a ON a.id = s.assignment_id
            JOIN users u ON u.id = s.student_id
            WHERE a.class_id IN ({ph})
            ORDER BY a.created_at DESC, u.display_name
        """, class_ids).fetchall()

        # Latest thing the student SAID (needs_help etc.), not status transitions.
        notes = {}
        for e in conn.execute("SELECT actor_id, assignment_id, detail FROM events WHERE type = 'student_update' ORDER BY id").fetchall():
            notes[(e["actor_id"], e["assignment_id"])] = e["detail"]

        # Latest submission per student-assignment.
        latest_sub = {}
        for s in conn.execute("SELECT assignment_id, student_id, kind, content, created_at FROM submissions ORDER BY id").fetchall():
            latest_sub[(s["student_id"], s["assignment_id"])] = s

        recent = conn.execute(f"""
            SELECT e.type, e.detail, e.created_at, u.display_name, a.title
            FROM events e
            LEFT JOIN users u ON u.id = e.actor_id
            LEFT JOIN assignments a ON a.id = e.assignment_id
            WHERE a.class_id IN ({ph}) OR e.assignment_id IS NULL
            ORDER BY e.id DESC LIMIT 12
        """, class_ids).fetchall()

    attention, assignments = [], {}
    for r in rows:
        due = datetime.fromisoformat(r["due_at"])
        level, reason = attention_level(r["status"], r["telegram_id"], due, now)
        sub = latest_sub.get((r["student_id"], r["assignment_id"]))
        item = {
            "assignment_id": r["assignment_id"], "student_id": r["student_id"],
            "student": r["display_name"], "assignment": r["title"], "status": r["status"],
            "level": level, "reason": reason, "due": fmt(r["due_at"]),
            "note": notes.get((r["student_id"], r["assignment_id"])),
            "submission": None if sub is None else {
                "kind": sub["kind"], "text": sub["content"] if sub["kind"] == "text" else None, "at": fmt(sub["created_at"])},
        }
        if level >= 1:
            attention.append(item)
        a = assignments.setdefault(r["assignment_id"], {"id": r["assignment_id"], "title": r["title"], "subject": r["subject"], "due": fmt(r["due_at"]), "students": []})
        a["students"].append({"name": r["display_name"], "status": r["status"], "level": level})

    attention.sort(key=lambda x: -x["level"])
    return {"name": user["display_name"], "classes": [dict(c) for c in classes],
            "attention": attention, "assignments": list(assignments.values()), "recent": [dict(e) for e in recent]}


# ============ API: feedback (teacher -> student) ============

class FeedbackIn(BaseModel):
    assignment_id: int
    student_id: int
    text: str = Field(min_length=1, max_length=1000)
    decision: Literal["approve", "revise"]

@app.post("/api/feedback")
def send_feedback(body: FeedbackIn, x_init_data: str = Header(default="")):
    teacher = current_user(x_init_data)
    with db.connect() as conn:
        a = conn.execute("SELECT * FROM assignments WHERE id = ?", (body.assignment_id,)).fetchone()
        # AUTHORIZATION: the assignment must belong to a class this teacher teaches.
        if a is None or a["class_id"] not in teacher_class_ids(conn, teacher["id"]):
            raise HTTPException(status_code=403, detail="You do not teach this class.")
        row = conn.execute("SELECT * FROM assignment_students WHERE assignment_id = ? AND student_id = ?",
                           (body.assignment_id, body.student_id)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="That student is not on this assignment.")
        student = conn.execute("SELECT * FROM users WHERE id = ?", (body.student_id,)).fetchone()

        new_status = "completed" if body.decision == "approve" else "in_progress"
        conn.execute("INSERT INTO feedback (assignment_id, student_id, teacher_id, text, decision, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                     (body.assignment_id, body.student_id, teacher["id"], body.text, body.decision, now_iso()))
        conn.execute("UPDATE assignment_students SET status = ?, updated_at = ? WHERE assignment_id = ? AND student_id = ?",
                     (new_status, now_iso(), body.assignment_id, body.student_id))

    db.log_event("feedback_sent", actor_id=teacher["id"], assignment_id=body.assignment_id,
                 detail=f"to {student['display_name']}: {body.decision}")

    if student["telegram_id"] is not None:
        headline = "✅ Approved" if body.decision == "approve" else "✏️ Needs changes"
        send_telegram(student["telegram_id"], f"{headline} — {a['title']}\n\nFeedback from {teacher['display_name']}:\n{body.text}")
    return {"ok": True, "new_status": new_status}


class RemindIn(BaseModel):
    assignment_id: int

@app.post("/api/remind")
def remind_now(body: RemindIn, x_init_data: str = Header(default="")):
    """Teacher acts from where she sees the state: run the reminder job for ONE assignment, now.
    Same rules as the scheduled job (blocked -> teacher, silent -> student, idempotent); quiet hours are ignored
    because this is an explicit teacher action, not an automatic one."""
    teacher = current_user(x_init_data)
    with db.connect() as conn:
        a = conn.execute("SELECT * FROM assignments WHERE id = ?", (body.assignment_id,)).fetchone()
        if a is None or a["class_id"] not in teacher_class_ids(conn, teacher["id"]):
            raise HTTPException(status_code=403, detail="You do not teach this class.")
    summary = reminders.run_reminders(demo=True, assignment_id=body.assignment_id)
    return {"ok": True, **summary}


# ============ API: student dashboard ============

@app.get("/api/student")
def student_dashboard(x_init_data: str = Header(default="")):
    user = current_user(x_init_data)
    with db.connect() as conn:
        rows = conn.execute("""
            SELECT a.id, a.title, a.subject, a.instructions, a.due_at, s.status,
                   (SELECT COUNT(*) FROM submissions sub WHERE sub.assignment_id = a.id AND sub.student_id = s.student_id) AS submissions
            FROM assignment_students s JOIN assignments a ON a.id = s.assignment_id
            WHERE s.student_id = ?
            ORDER BY a.due_at
        """, (user["id"],)).fetchall()
        fb = conn.execute("""
            SELECT f.assignment_id, f.text, f.decision, f.created_at, u.display_name AS teacher
            FROM feedback f JOIN users u ON u.id = f.teacher_id
            WHERE f.student_id = ? ORDER BY f.id DESC
        """, (user["id"],)).fetchall()
    feedback_by_assignment = {}
    for f in fb:
        feedback_by_assignment.setdefault(f["assignment_id"], []).append(
            {"text": f["text"], "decision": f["decision"], "at": fmt(f["created_at"]), "teacher": f["teacher"]})
    return {"name": user["display_name"],
            "assignments": [dict(r, due=fmt(r["due_at"]), feedback=feedback_by_assignment.get(r["id"], [])) for r in rows]}


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
    textarea { width: 100%; box-sizing: border-box; min-height: 56px; margin-top: 8px; border-radius: 8px; padding: 6px; }
    button { padding: 8px 12px; border-radius: 8px; border: 0; margin: 6px 6px 0 0; font-size: 14px; }
    .ok { background: #3dbb6a; color: #fff; } .rev { background: #f0a020; color: #fff; }
    .fb { border-top: 1px dashed rgba(128,128,128,.4); margin-top: 8px; padding-top: 6px; font-size: 13px; }
  </style>
</head>
<body>
  <div id="out">Loading...</div>
  <script>
    const tg = window.Telegram.WebApp; tg.ready(); tg.expand();
    const H = { 'X-Init-Data': tg.initData };
    const out = document.getElementById('out');
    const esc = s => String(s ?? '').replace(/[&<>]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));

    async function api(path, body) {
      const r = await fetch(path, body ? { method: 'POST', headers: { ...H, 'Content-Type': 'application/json' }, body: JSON.stringify(body) } : { headers: H });
      const data = await r.json();
      if (!r.ok) throw new Error(typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail));
      return data;
    }

    async function remindNow(aid) {
      try {
        const r = await api('/api/remind', { assignment_id: aid });
        alert(`Reminders run.\nTo students: ${r.sent}\nTo you (blocked): ${r.teacher_nudges}\nAlready reminded / nothing due: ${r.skipped}`);
        load();
      } catch (e) { alert('Could not run: ' + e.message); }
    }

    async function giveFeedback(aid, sid, decision, key) {
      const text = document.getElementById('fb-' + key).value.trim();
      if (!text) { alert('Write a line of feedback first.'); return; }
      try { await api('/api/feedback', { assignment_id: aid, student_id: sid, text, decision }); load(); }
      catch (e) { alert('Could not send: ' + e.message); }
    }

    function teacherView(d) {
      let h = `<h2>Hello ${esc(d.name)}</h2><div class="muted">${d.classes.map(c => esc(c.name)+' · '+esc(c.subject)).join(', ')}</div>`;
      h += `<h3>Needs attention now (${d.attention.length})</h3>`;
      if (!d.attention.length) h += `<div class="muted">Everyone is on track. 🎉</div>`;
      d.attention.forEach((a, i) => {
        h += `<div class="card lvl${a.level}"><div class="row"><b>${esc(a.student)}</b><span class="pill">${esc(a.status)}</span></div>
              <div>${esc(a.reason)}</div><div class="muted">${esc(a.assignment)} · due ${esc(a.due)}</div>
              ${a.note ? `<div class="muted">“${esc(a.note)}”</div>` : ''}`;
        if (a.submission) h += `<div class="muted">Submitted ${esc(a.submission.kind)} at ${esc(a.submission.at)}${a.submission.text ? ': “' + esc(a.submission.text) + '”' : ''}</div>`;
        if (a.status === 'submitted') {
          h += `<textarea id="fb-${i}" placeholder="Feedback for ${esc(a.student)}..."></textarea>
                <button class="ok" onclick="giveFeedback(${a.assignment_id}, ${a.student_id}, 'approve', ${i})">✅ Approve</button>
                <button class="rev" onclick="giveFeedback(${a.assignment_id}, ${a.student_id}, 'revise', ${i})">✏️ Needs changes</button>`;
        }
        h += `</div>`;
      });
      h += `<h3>Assignments</h3>`;
      for (const a of d.assignments) {
        h += `<div class="card"><div class="row"><b>${esc(a.title)}</b><span class="muted">due ${esc(a.due)}</span></div>
              <div>${a.students.map(s => `<span class="pill">${esc(s.name)}: ${esc(s.status)}</span>`).join('')}</div>
              <button class="rev" onclick="remindNow(${a.id})">⏰ Remind now</button></div>`;
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
              <div class="muted">Submissions: ${a.submissions}</div>`;
        for (const f of a.feedback) {
          h += `<div class="fb"><b>${f.decision === 'approve' ? '✅ Approved' : '✏️ Needs changes'}</b> · ${esc(f.teacher)} · ${esc(f.at)}<br>${esc(f.text)}</div>`;
        }
        h += `</div>`;
      }
      return h;
    }

    async function load() {
      try {
        const me = await api('/api/me');
        const d = await api(me.view === 'teacher' ? '/api/teacher' : '/api/student');
        out.innerHTML = me.view === 'teacher' ? teacherView(d) : studentView(d);
      } catch (e) { out.textContent = 'Access denied: ' + e.message; }
    }
    load();
  </script>
</body>
</html>"""

@app.get("/", response_class=HTMLResponse)
def home():
    return PAGE