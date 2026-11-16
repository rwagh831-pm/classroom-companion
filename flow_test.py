"""
End-to-end CHAT FLOW test - no Telegram needed.

Feeds fake Telegram updates straight into bot.handle_update and prints every reply the bot would send,
so the whole scenario (link -> assign -> confirm -> acknowledge -> blocked -> submit -> summary -> change deadline)
plus the attack cases (strangers, reused codes, malformed buttons, double taps, replayed updates) can be
exercised in ~30 seconds.

Needs ANTHROPIC_API_KEY (it exercises the real language model - this doubles as the NL-interpretation eval).
Uses a throw-away database; your classroom.db is untouched.

Run:  python flow_test.py
Read the output top to bottom; every block shows the input and the replies. "EXC" anywhere = a bug.
"""
import os
import db
db.DB_FILE = "flow_test.db"
if os.path.exists(db.DB_FILE):
    os.remove(db.DB_FILE)
db.init_db()
db.seed()

import bot, reminders, tg

OUT = []
def fake_send(chat_id, text, buttons=None, keyboard=None):
    OUT.append((chat_id, text[:100].replace("\n", " | "), "buttons" if buttons else ("keyboard" if keyboard else "")))
def fake_edit(chat_id, message_id, text):
    OUT.append((chat_id, "EDIT: " + text[:100].replace("\n", " | "), ""))
bot.send_message = fake_send
bot.edit_message = fake_edit
bot.answer_callback = lambda cid: None
tg.send_message = lambda chat_id, text, buttons=None: OUT.append((chat_id, "JOB: " + text[:100], ""))
reminders.send_message = tg.send_message

T, S1, STRANGER = 111, 222, 444          # fake Telegram ids: teacher, student, unknown person
with db.connect() as c:                  # the seed may have pre-linked the teacher to a real phone; use our fake id
    c.execute("UPDATE users SET telegram_id = ? WHERE id = 1", (T,))

UID = [1000]
def upd(**kw):
    UID[0] += 1
    return {"update_id": UID[0], **kw}
def msg(tg_id, text=None, **extra):
    m = {"message_id": UID[0], "chat": {"id": tg_id}, "from": {"id": tg_id, "first_name": "X"}}
    if text is not None:
        m["text"] = text
    m.update(extra)
    return upd(message=m)
def cb(tg_id, data):
    return upd(callback_query={"id": "c", "from": {"id": tg_id}, "data": data, "message": {"chat": {"id": tg_id}, "message_id": 1}})

FAILS = 0
def run(label, update, expect=None):
    """expect: a substring that must appear in some reply (optional)."""
    global FAILS
    OUT.clear()
    try:
        bot.handle_update(update)
        status = "ok"
    except Exception as e:
        status = f"EXC {type(e).__name__}: {e}"
        FAILS += 1
    if expect and not any(expect in o[1] for o in OUT):
        status += f"   <-- EXPECTED '{expect}'"
        FAILS += 1
    print(f"\n## {label} -> {status}")
    for o in OUT:
        print("   ", o)

run("stranger sends a photo before linking", msg(STRANGER, photo=[{"file_id": "p"}]), expect="Welcome")
run("stranger: lowercase code with spaces", msg(S1, "  s-8a-aarav "), expect="Linked! You are Aarav")
run("stranger: reuse the same code from another phone", msg(STRANGER, "S-8A-AARAV"), expect="already been used")
run("stranger taps a button before linking", cb(STRANGER, "ack:1"))
run("student: /remind", msg(S1, "/remind"), expect="Only teachers")
run("student: photo with no assignment", msg(S1, photo=[{"file_id": "p"}]), expect="no open assignments")
run("teacher: sticker (no text)", msg(T, sticker={"file_id": "s"}), expect="only read text")
run("teacher: 'hello' is not an assignment", msg(T, "hello"), expect="doesn't look like an assignment")
run("teacher: no deadline -> asks", msg(T, "Read chapter 3 and make notes"), expect="Quick question")
run("teacher: real assignment -> preview", msg(T, "Do exercise 4.2 q1 to q8, bring it day after"), expect="Preview")
draft = next(iter(bot.pending))
run("someone else taps the teacher's Confirm", cb(S1, f"confirm:{draft}"))
run("teacher taps Confirm", cb(T, f"confirm:{draft}"), expect="Sent:")
run("teacher double-taps Confirm", cb(T, f"confirm:{draft}"), expect="expired")
run("malformed button data", cb(T, "confirm"))
run("malformed button data 2", cb(S1, "ack:abc"))
run("student taps Got it on someone else's assignment", cb(S1, "ack:999"), expect="not yours")
run("student taps Got it", cb(S1, "ack:1"), expect="marked")
run("student taps Got it again", cb(S1, "ack:1"), expect="Already noted")
run("student: 'done half'", msg(S1, "done half will finish tonight"), expect="in progress")
run("student: 'no book cant do it' -> blocked + teacher pinged", msg(S1, "no book cant do it"), expect="blocked")
run("teacher: reminders -> blocked student escalates to teacher", msg(T, "/remind force"), expect="Teacher nudges: 1")
run("teacher: reminders again -> nothing repeats", msg(T, "/remind force"), expect="Teacher nudges: 0")
run("student: photo submission", msg(S1, photo=[{"file_id": "p1"}, {"file_id": "p2"}]), expect="Submitted (photo)")
run("student taps Got it after submitting -> never backwards", cb(S1, "ack:1"), expect="is submitted")
run("teacher: 'who is at risk this week?'", msg(T, "who is at risk this week?"), expect="📝")
run("teacher: deadline in the past -> asks (safe outcome: a question, never a save)", msg(T, "move it to yesterday"), expect="Quick question")
run("teacher: 'move it to Friday evening' -> change preview", msg(T, "move it to Friday evening"), expect="Change deadline")
chg = next(iter(bot.pending))
run("teacher confirms the change -> student told", cb(T, f"confirm:{chg}"), expect="Deadline changed")
run("teacher: second assignment", msg(T, "Write a paragraph on the monsoon, due tomorrow evening"), expect="Preview")
run("teacher confirms second", cb(T, f"confirm:{next(iter(bot.pending))}"), expect="Sent:")
run("student: 'done half' on latest", msg(S1, "done half"), expect="in progress")
run("teacher: third assignment", msg(T, "Learn the 7 times table, test on Friday morning"), expect="Preview")
run("teacher confirms third", cb(T, f"confirm:{next(iter(bot.pending))}"), expect="Sent:")
run("student photo with TWO open assignments -> asked which", msg(S1, photo=[{"file_id": "p3"}]), expect="Which assignment")
sub_key = next(iter(bot.pending_submissions))
run("student picks the monsoon one", cb(S1, f"sub:{sub_key}:2"), expect="Submitted (photo) for: ")
run("student taps the same picker again -> expired", cb(S1, f"sub:{sub_key}:2"), expect="expired")
run("replayed update_id is ignored", {"update_id": UID[0], "message": {"message_id": 1, "chat": {"id": T}, "from": {"id": T}, "text": "/start"}})
run("edited_message update type is ignored", upd(edited_message={"chat": {"id": T}, "from": {"id": T}, "text": "x"}))

print("\n" + ("ALL FLOWS OK" if FAILS == 0 else f"{FAILS} PROBLEM(S) - look for EXC / EXPECTED above"))
os.remove(db.DB_FILE)
