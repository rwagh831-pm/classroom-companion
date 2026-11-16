"""
Classroom Companion - test cases.

Run:  python tests.py
Needs no network and no API key: these test the RULES (the parts that must never be wrong),
not the language model. Model behaviour is covered by the manual scenario in README.md.

Uses a throw-away database file so your real classroom.db is never touched.
"""
import os
import hmac
import hashlib
import json
import unittest
from datetime import datetime, timedelta
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

import db
db.DB_FILE = "test_classroom.db"           # must be set BEFORE anything connects

import reminders
import app
import bot

IST = ZoneInfo("Asia/Kolkata")
NOW = datetime(2026, 11, 16, 10, 0, tzinfo=IST)      # a Monday, 10:00, outside quiet hours


def fresh_db():
    if os.path.exists(db.DB_FILE):
        os.remove(db.DB_FILE)
    db.init_db()
    db.seed()


class Test1_ReminderRules(unittest.TestCase):
    """Reminder job behaves differently for blocked vs silent vs overdue vs done; quiet hours are code, not model."""

    def test_blocked_student_nudges_teacher_not_student(self):
        kind = reminders.decide("blocked", NOW + timedelta(days=2), NOW - timedelta(hours=3), NOW)
        self.assertEqual(kind, "blocked_teacher")

    def test_silent_student_gets_not_seen_after_threshold(self):
        kind = reminders.decide("assigned", NOW + timedelta(days=2), NOW - timedelta(hours=2), NOW)
        self.assertEqual(kind, "not_seen")

    def test_silent_student_too_recent_is_left_alone(self):
        kind = reminders.decide("assigned", NOW + timedelta(days=2), NOW - timedelta(minutes=10), NOW)
        self.assertIsNone(kind)

    def test_overdue_beats_everything_except_blocked_and_done(self):
        self.assertEqual(reminders.decide("acknowledged", NOW - timedelta(hours=1), NOW - timedelta(days=2), NOW), "overdue")
        self.assertEqual(reminders.decide("blocked", NOW - timedelta(hours=1), NOW - timedelta(days=2), NOW), "blocked_teacher")

    def test_submitted_and_completed_never_reminded(self):
        for status in ("submitted", "completed", "cancelled"):
            self.assertIsNone(reminders.decide(status, NOW - timedelta(hours=1), NOW - timedelta(days=2), NOW))

    def test_due_soon_window(self):
        self.assertEqual(reminders.decide("in_progress", NOW + timedelta(hours=5), NOW - timedelta(days=1), NOW), "due_soon")
        self.assertIsNone(reminders.decide("in_progress", NOW + timedelta(days=3), NOW - timedelta(days=1), NOW))

    def test_quiet_hours_21_to_07_ist(self):
        self.assertTrue(reminders.is_quiet_hours(NOW.replace(hour=22)))
        self.assertTrue(reminders.is_quiet_hours(NOW.replace(hour=6)))
        self.assertFalse(reminders.is_quiet_hours(NOW.replace(hour=7)))
        self.assertFalse(reminders.is_quiet_hours(NOW.replace(hour=20)))


class Test2_AttentionBoardAndStateRules(unittest.TestCase):
    """Who needs attention is decided by code from state + time; statuses never move backwards after a submission."""

    def test_attention_levels_rank_correctly(self):
        future, past = NOW + timedelta(days=1), NOW - timedelta(days=1)
        self.assertEqual(app.attention_level("blocked", 111, future, NOW)[0], 3)
        self.assertEqual(app.attention_level("assigned", 111, past, NOW)[0], 3)     # overdue + never acknowledged
        self.assertEqual(app.attention_level("assigned", None, future, NOW)[0], 2)  # not linked
        self.assertEqual(app.attention_level("assigned", 111, future, NOW)[0], 2)   # silent
        self.assertEqual(app.attention_level("submitted", 111, future, NOW)[0], 1)  # review
        self.assertEqual(app.attention_level("in_progress", 111, future, NOW)[0], 0)
        self.assertEqual(app.attention_level("completed", 111, past, NOW)[0], 0)    # done, even if "overdue"

    def test_did_not_see_vs_did_not_do(self):
        past = NOW - timedelta(days=1)
        self.assertIn("did not see it", app.attention_level("assigned", 111, past, NOW)[1])
        self.assertIn("has not submitted", app.attention_level("acknowledged", 111, past, NOW)[1])

    def test_status_never_moves_backwards_from_submission(self):
        self.assertFalse(bot.can_move("submitted", "in_progress"))
        self.assertFalse(bot.can_move("completed", "blocked"))
        self.assertTrue(bot.can_move("acknowledged", "blocked"))
        self.assertFalse(bot.can_move("blocked", "blocked"))
        self.assertFalse(bot.can_move("assigned", None))          # needs_help changes nothing


class Test3_TrustBoundaries(unittest.TestCase):
    """Access, identity and idempotency are enforced on the server."""

    def test_mini_app_rejects_forged_and_accepts_signed_init_data(self):
        app.BOT_TOKEN = "123:TEST-TOKEN"
        user = json.dumps({"id": 42, "first_name": "Priya"})
        fields = {"auth_date": "1700000000", "query_id": "abc", "user": user}
        check = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
        secret = hmac.new(b"WebAppData", app.BOT_TOKEN.encode(), hashlib.sha256).digest()
        good_hash = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()

        self.assertEqual(app.verify_init_data(urlencode({**fields, "hash": good_hash}))["id"], 42)
        tampered = json.dumps({"id": 1, "first_name": "Priya"})       # claims to be user 1
        self.assertIsNone(app.verify_init_data(urlencode({**fields, "user": tampered, "hash": good_hash})))
        self.assertIsNone(app.verify_init_data(urlencode(fields)))    # no hash at all

    def test_invite_code_cannot_be_reused_or_guessed(self):
        fresh_db()
        reply, user = bot.try_link(5001, "S-8A-AARAV")
        self.assertIsNotNone(user)
        reply, user = bot.try_link(5002, "S-8A-AARAV")                 # second phone, same code
        self.assertIsNone(user)
        reply, user = bot.try_link(5003, "S-8A-NOBODY")
        self.assertIsNone(user)

    def test_duplicate_telegram_update_is_ignored(self):
        fresh_db()
        self.assertTrue(db.mark_update_processed(999_001))
        self.assertFalse(db.mark_update_processed(999_001))           # same update delivered again
        self.assertTrue(db.mark_update_processed(999_002))

    def test_deadline_change_resets_only_what_it_should(self):
        fresh_db()
        with db.connect() as conn:
            conn.execute("INSERT INTO assignments (id, class_id, created_by, subject, title, instructions, due_at, created_at) "
                         "VALUES (10, 1, 1, 'Maths', 'Ex 4.2', 'Q1-Q8', ?, ?)", ((NOW + timedelta(days=1)).isoformat(), NOW.isoformat()))
            conn.execute("INSERT INTO assignment_students VALUES (10, 2, 'acknowledged', ?)", (NOW.isoformat(),))
            conn.execute("INSERT INTO assignment_students VALUES (10, 3, 'in_progress', ?)", (NOW.isoformat(),))
            for kind in ("not_seen", "due_soon", "overdue"):
                conn.execute("INSERT INTO reminders (assignment_id, student_id, kind, sent_at) VALUES (10, 2, ?, ?)", (kind, NOW.isoformat()))

        bot.change_deadline(10, (NOW + timedelta(days=4)).isoformat())

        with db.connect() as conn:
            rows = {r["student_id"]: r["status"] for r in conn.execute("SELECT * FROM assignment_students WHERE assignment_id = 10")}
            kinds = {r["kind"] for r in conn.execute("SELECT kind FROM reminders WHERE assignment_id = 10")}
        self.assertEqual(rows[2], "assigned")        # acknowledged -> must see the new date again
        self.assertEqual(rows[3], "in_progress")     # already working -> untouched
        self.assertEqual(kinds, {"not_seen"})        # date-based reminders may fire again; not_seen stays


if __name__ == "__main__":
    unittest.main(verbosity=2)
