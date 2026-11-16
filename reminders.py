import sys
import time
from datetime import datetime, timedelta

import db
import brain
from tg import send_message

QUIET_START, QUIET_END = 21, 7          # 21:00 to 07:00 IST: no messages to students
NOT_SEEN_AFTER = timedelta(hours=1)     # treat silence as silence after this long
DUE_SOON_WINDOW = timedelta(hours=24)


# ---------- RULES (pure, testable) ----------

def is_quiet_hours(now):
    return now.hour >= QUIET_START or now.hour < QUIET_END

def decide(status, due_at, created_at, now, demo=False):
    """Returns the reminder kind for one student-assignment, or None."""
    if status in ("submitted", "completed", "cancelled"):
        return None
    if status == "blocked":
        return "blocked_teacher"
    if now > due_at:
        return "overdue"
    if status == "assigned" and (demo or now - created_at >= NOT_SEEN_AFTER):
        return "not_seen"
    if due_at - now <= DUE_SOON_WINDOW:
        return "due_soon"
    return None


# ---------- THE JOB ----------

def run_reminders(demo=False, assignment_id=None):
    """demo=True ignores quiet hours and the silence threshold. assignment_id limits the run to one assignment
    (used by the teacher's 'Remind now' button in the Mini App)."""
    now = datetime.now(brain.SCHOOL_TZ)
    quiet = is_quiet_hours(now) and not demo
    summary = {"sent": 0, "teacher_nudges": 0, "deferred_quiet_hours": 0, "skipped": 0}

    sql = """
        SELECT a.id AS assignment_id, a.title, a.due_at, a.created_at, a.created_by,
               u.id AS student_id, u.display_name, u.telegram_id, s.status
        FROM assignment_students s
        JOIN assignments a ON a.id = s.assignment_id
        JOIN users u ON u.id = s.student_id
        WHERE a.status != 'cancelled'
    """
    params = ()
    if assignment_id is not None:
        sql += " AND a.id = ?"
        params = (assignment_id,)
    with db.connect() as conn:
        rows = conn.execute(sql, params).fetchall()

    for r in rows:
        due_at = datetime.fromisoformat(r["due_at"])
        created_at = datetime.fromisoformat(r["created_at"])
        kind = decide(r["status"], due_at, created_at, now, demo=demo)
        if kind is None or r["telegram_id"] is None:
            summary["skipped"] += 1
            continue

        # Idempotency: one reminder per kind per student per assignment. UNIQUE constraint enforces it.
        with db.connect() as conn:
            already = conn.execute(
                "SELECT 1 FROM reminders WHERE assignment_id = ? AND student_id = ? AND kind = ?",
                (r["assignment_id"], r["student_id"], kind)).fetchone()
        if already:
            summary["skipped"] += 1
            continue

        due_text = due_at.strftime("%a %d %b, %H:%M")

        if kind == "blocked_teacher":
            teacher = db.get_user(r["created_by"])
            if teacher["telegram_id"] is not None:
                send_message(teacher["telegram_id"],
                             f"⏰ {r['display_name']} is still blocked on '{r['title']}' (due {due_text}). They may need you.")
                summary["teacher_nudges"] += 1
        else:
            if quiet:
                db.log_event("reminder_deferred", actor_id=r["student_id"], assignment_id=r["assignment_id"], detail=f"{kind} (quiet hours)")
                summary["deferred_quiet_hours"] += 1
                continue
            text = brain.write_reminder(kind, r["display_name"], r["title"], due_text)
            send_message(r["telegram_id"], text)
            summary["sent"] += 1
            if kind == "overdue":
                teacher = db.get_user(r["created_by"])
                if teacher["telegram_id"] is not None:
                    send_message(teacher["telegram_id"], f"⏰ {r['display_name']} is overdue on '{r['title']}' (due {due_text}).")

        with db.connect() as conn:
            conn.execute("INSERT INTO reminders (assignment_id, student_id, kind, sent_at) VALUES (?, ?, ?, ?)",
                         (r["assignment_id"], r["student_id"], kind, now.isoformat(timespec='seconds')))
        db.log_event("reminder_sent", actor_id=r["student_id"], assignment_id=r["assignment_id"], detail=kind)

    print(f"[REMINDERS {now:%H:%M}] {summary}")
    return summary


if __name__ == "__main__":
    db.init_db()
    if len(sys.argv) > 1 and sys.argv[1] == "loop":
        print("Reminder job running every 10 minutes. Ctrl+C to stop.")
        while True:
            run_reminders()
            time.sleep(600)
    else:
        run_reminders(demo="demo" in sys.argv)