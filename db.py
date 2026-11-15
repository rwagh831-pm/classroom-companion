import sqlite3
from datetime import datetime

DB_FILE = "classroom.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS schools (
    id        INTEGER PRIMARY KEY,
    name      TEXT NOT NULL,
    timezone  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS classes (
    id         INTEGER PRIMARY KEY,
    school_id  INTEGER NOT NULL REFERENCES schools(id),
    name       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY,
    display_name  TEXT NOT NULL,
    telegram_id   INTEGER UNIQUE
);

CREATE TABLE IF NOT EXISTS memberships (
    id        INTEGER PRIMARY KEY,
    user_id   INTEGER NOT NULL REFERENCES users(id),
    class_id  INTEGER NOT NULL REFERENCES classes(id),
    role      TEXT NOT NULL CHECK (role IN ('teacher', 'student', 'coordinator')),
    subject   TEXT,
    UNIQUE (user_id, class_id, role)
);

CREATE TABLE IF NOT EXISTS invites (
    code                  TEXT PRIMARY KEY,
    class_id              INTEGER NOT NULL REFERENCES classes(id),
    role                  TEXT NOT NULL,
    for_user_id           INTEGER REFERENCES users(id),
    used_by_telegram_id   INTEGER
);

CREATE TABLE IF NOT EXISTS assignments (
    id            INTEGER PRIMARY KEY,
    class_id      INTEGER NOT NULL REFERENCES classes(id),
    created_by    INTEGER NOT NULL REFERENCES users(id),
    subject       TEXT,
    title         TEXT NOT NULL,
    instructions  TEXT NOT NULL,
    due_at        TEXT NOT NULL,
    status        TEXT NOT NULL DEFAULT 'assigned',
    created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS assignment_students (
    assignment_id  INTEGER NOT NULL REFERENCES assignments(id),
    student_id     INTEGER NOT NULL REFERENCES users(id),
    status         TEXT NOT NULL DEFAULT 'assigned',
    updated_at     TEXT NOT NULL,
    PRIMARY KEY (assignment_id, student_id)
);

CREATE TABLE IF NOT EXISTS submissions (
    id             INTEGER PRIMARY KEY,
    assignment_id  INTEGER NOT NULL REFERENCES assignments(id),
    student_id     INTEGER NOT NULL REFERENCES users(id),
    kind           TEXT NOT NULL CHECK (kind IN ('text', 'photo', 'file')),
    content        TEXT NOT NULL,
    created_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS feedback (
    id             INTEGER PRIMARY KEY,
    assignment_id  INTEGER NOT NULL REFERENCES assignments(id),
    student_id     INTEGER NOT NULL REFERENCES users(id),
    teacher_id     INTEGER NOT NULL REFERENCES users(id),
    text           TEXT NOT NULL,
    decision       TEXT NOT NULL CHECK (decision IN ('approve', 'revise')),
    created_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS reminders (
    id             INTEGER PRIMARY KEY,
    assignment_id  INTEGER NOT NULL REFERENCES assignments(id),
    student_id     INTEGER NOT NULL REFERENCES users(id),
    kind           TEXT NOT NULL,
    sent_at        TEXT NOT NULL,
    UNIQUE (assignment_id, student_id, kind)
);

CREATE TABLE IF NOT EXISTS events (
    id             INTEGER PRIMARY KEY,
    type           TEXT NOT NULL,
    actor_id       INTEGER,
    assignment_id  INTEGER,
    detail         TEXT,
    created_at     TEXT NOT NULL
);
"""


def connect():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    with connect() as conn:
        conn.executescript(SCHEMA)


def seed():
    with connect() as conn:
        conn.execute("INSERT OR IGNORE INTO schools (id, name, timezone) VALUES (1, 'Greenfield Public School', 'Asia/Kolkata')")
        conn.execute("INSERT OR IGNORE INTO classes (id, school_id, name) VALUES (1, 1, '8A'), (2, 1, '8B')")
        conn.execute("INSERT OR IGNORE INTO users (id, display_name) VALUES (1, 'Priya (Maths)'), (2, 'Aarav'), (3, 'Diya')")
        conn.execute("INSERT OR IGNORE INTO memberships (user_id, class_id, role, subject) VALUES (1, 1, 'teacher', 'Maths'), (2, 1, 'student', NULL), (3, 1, 'student', NULL)")
        conn.execute("INSERT OR IGNORE INTO invites (code, class_id, role, for_user_id) VALUES ('T-8A-PRIYA', 1, 'teacher', 1), ('S-8A-AARAV', 1, 'student', 2), ('S-8A-DIYA', 1, 'student', 3)")


def log_event(event_type, actor_id=None, assignment_id=None, detail=None):
    """Write one line to the events table AND print it - our observable log."""
    now = datetime.now().isoformat(timespec="seconds")
    with connect() as conn:
        conn.execute(
            "INSERT INTO events (type, actor_id, assignment_id, detail, created_at) VALUES (?, ?, ?, ?, ?)",
            (event_type, actor_id, assignment_id, detail, now),
        )
    print(f"[EVENT {now}] {event_type} actor={actor_id} assignment={assignment_id} {detail or ''}")


if __name__ == "__main__":
    init_db()
    seed()
    with connect() as conn:
        rows = conn.execute("""
            SELECT users.display_name, memberships.role, memberships.subject, classes.name AS class_name
            FROM memberships
            JOIN users   ON users.id   = memberships.user_id
            JOIN classes ON classes.id = memberships.class_id
        """).fetchall()
    for row in rows:
        subject = f" ({row['subject']})" if row["subject"] else ""
        print(row["display_name"], "is a", row["role"] + subject, "in", row["class_name"])
    print("\nDatabase ready:", DB_FILE)