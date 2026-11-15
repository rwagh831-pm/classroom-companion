import sqlite3

DB_FILE = "classroom.db"

# The blueprint for our filing cabinet: five drawers (tables).
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
"""


def connect():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row            # lets us read columns by name: row["name"]
    conn.execute("PRAGMA foreign_keys = ON")  # make SQLite enforce the arrows between tables
    return conn


def init_db():
    with connect() as conn:
        conn.executescript(SCHEMA)


def seed():
    # The pilot from Appendix A. "OR IGNORE" = skip if already there, so re-running is safe.
    with connect() as conn:
        conn.execute("INSERT OR IGNORE INTO schools (id, name, timezone) VALUES (1, 'Greenfield Public School', 'Asia/Kolkata')")
        conn.execute("INSERT OR IGNORE INTO classes (id, school_id, name) VALUES (1, 1, '8A'), (2, 1, '8B')")
        conn.execute("INSERT OR IGNORE INTO users (id, display_name) VALUES (1, 'Priya (Maths)'), (2, 'Aarav'), (3, 'Diya')")
        conn.execute("INSERT OR IGNORE INTO memberships (user_id, class_id, role, subject) VALUES (1, 1, 'teacher', 'Maths'), (2, 1, 'student', NULL), (3, 1, 'student', NULL)")
        conn.execute("INSERT OR IGNORE INTO invites (code, class_id, role, for_user_id) VALUES ('T-8A-PRIYA', 1, 'teacher', 1), ('S-8A-AARAV', 1, 'student', 2), ('S-8A-DIYA', 1, 'student', 3)")


# This block runs ONLY when you run "python db.py" directly,
# not when another file does "import db" later.
if __name__ == "__main__":
    init_db()
    seed()
    with connect() as conn:
        rows = conn.execute("""
            SELECT users.display_name, memberships.role, classes.name AS class_name
            FROM memberships
            JOIN users   ON users.id   = memberships.user_id
            JOIN classes ON classes.id = memberships.class_id
        """).fetchall()
    for row in rows:
        print(row["display_name"], "is a", row["role"], "in", row["class_name"])
    print("\nDatabase ready:", DB_FILE)