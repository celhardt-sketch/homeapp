import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path

DATA_DIR = Path(os.environ.get("DATA_DIR", "/data"))
DB_PATH = DATA_DIR / "home_maintenance.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS rooms (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    slug TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    icon TEXT NOT NULL DEFAULT 'home',
    sort_order INTEGER NOT NULL DEFAULT 0,
    active INTEGER NOT NULL DEFAULT 1
);

-- Old slugs after a rename, so NFC tags written with them keep resolving.
CREATE TABLE IF NOT EXISTS room_slug_aliases (
    slug TEXT PRIMARY KEY,
    room_id INTEGER NOT NULL REFERENCES rooms(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE COLLATE NOCASE,
    role TEXT NOT NULL DEFAULT 'member',   -- 'admin' | 'member'
    password_hash TEXT NOT NULL,
    token_version TEXT NOT NULL DEFAULT '0',
    email TEXT NOT NULL DEFAULT '',
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);

-- room_id NULL = a standalone errand; assignee_id = whose list it is on; due_on = one-off due date
CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    room_id INTEGER REFERENCES rooms(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    frequency_days INTEGER,
    sort_order INTEGER NOT NULL DEFAULT 0,
    active INTEGER NOT NULL DEFAULT 1,
    assignee_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    due_on TEXT,
    created_at TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS completions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    completed_by TEXT NOT NULL,
    completed_at TEXT NOT NULL,
    user_id INTEGER REFERENCES users(id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS idx_completions_task ON completions(task_id, completed_at DESC);

CREATE TABLE IF NOT EXISTS notes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    author TEXT NOT NULL,
    body TEXT NOT NULL,
    needs_purchase INTEGER NOT NULL DEFAULT 0,
    resolved INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_notes_task ON notes(task_id, resolved);

CREATE TABLE IF NOT EXISTS pantry_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    category TEXT NOT NULL DEFAULT '',
    quantity TEXT NOT NULL DEFAULT '',
    low INTEGER NOT NULL DEFAULT 0,
    updated_by TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS medications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    person TEXT NOT NULL,
    reorder_days INTEGER NOT NULL DEFAULT 28,
    notes TEXT NOT NULL DEFAULT '',
    active INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS med_pickups (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    medication_id INTEGER NOT NULL REFERENCES medications(id) ON DELETE CASCADE,
    picked_up_on TEXT NOT NULL,
    picked_up_by TEXT NOT NULL DEFAULT '',
    reminder_sent_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_med_pickups ON med_pickups(medication_id, picked_up_on DESC);

CREATE TABLE IF NOT EXISTS children (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS prescriptions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    child_id INTEGER NOT NULL REFERENCES children(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    pharmacy TEXT NOT NULL DEFAULT '',
    contact_name TEXT NOT NULL DEFAULT '',
    contact_phone TEXT NOT NULL DEFAULT '',
    days_supply INTEGER NOT NULL DEFAULT 30,
    refill_after_days INTEGER NOT NULL DEFAULT 28,
    active INTEGER NOT NULL DEFAULT 1,
    notes TEXT NOT NULL DEFAULT '',
    assignee_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    called_on TEXT,
    called_by TEXT NOT NULL DEFAULT '',
    called_notes TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS pickups (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    prescription_id INTEGER NOT NULL REFERENCES prescriptions(id) ON DELETE CASCADE,
    picked_up_on TEXT NOT NULL,
    picked_up_by TEXT NOT NULL DEFAULT '',
    user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    notes TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_pickups ON pickups(prescription_id, picked_up_on DESC);

CREATE TABLE IF NOT EXISTS upkeep_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    category TEXT NOT NULL DEFAULT '',
    interval_days INTEGER NOT NULL DEFAULT 90,
    notes TEXT NOT NULL DEFAULT '',
    active INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS upkeep_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id INTEGER NOT NULL REFERENCES upkeep_items(id) ON DELETE CASCADE,
    done_on TEXT NOT NULL,
    done_by TEXT NOT NULL DEFAULT '',
    note TEXT NOT NULL DEFAULT '',
    reminder_sent_at TEXT,
    user_id INTEGER REFERENCES users(id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS idx_upkeep_logs ON upkeep_logs(item_id, done_on DESC);

-- Standalone shopping items (the To Buy list also shows purchase notes and low pantry items)
CREATE TABLE IF NOT EXISTS shopping_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    notes TEXT NOT NULL DEFAULT '',
    assignee_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    due_on TEXT,
    added_by TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    bought_at TEXT,
    bought_by TEXT NOT NULL DEFAULT ''
);

-- One row per assignment; the reminder loop emails unsent ones and the app shows unread ones
CREATE TABLE IF NOT EXISTS notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,                 -- 'task' | 'shopping'
    ref_id INTEGER NOT NULL,
    message TEXT NOT NULL,
    created_by TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    read_at TEXT,
    emailed_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_notifications_user ON notifications(user_id, read_at);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- OAuth 2.1 authorization server state for the MCP connector (see oauth.py)
CREATE TABLE IF NOT EXISTS oauth_clients (
    client_id TEXT PRIMARY KEY,
    data TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS oauth_pending (
    id TEXT PRIMARY KEY,
    client_id TEXT NOT NULL,
    data TEXT NOT NULL,
    expires_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS oauth_codes (
    code TEXT PRIMARY KEY,
    client_id TEXT NOT NULL,
    data TEXT NOT NULL,
    expires_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS oauth_tokens (
    token TEXT PRIMARY KEY,
    kind TEXT NOT NULL,              -- 'access' | 'refresh'
    client_id TEXT NOT NULL,
    scopes TEXT NOT NULL DEFAULT '',
    pair TEXT,                       -- the other token issued alongside this one
    expires_at REAL,
    created_at TEXT NOT NULL,
    last_used_at TEXT
);

CREATE TABLE IF NOT EXISTS idempotency_keys (
    key TEXT PRIMARY KEY,
    tool TEXT NOT NULL,
    response TEXT NOT NULL,
    created_at REAL NOT NULL
);
"""


def _cols(conn, table: str) -> set[str]:
    return {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}


def _rebuild_tasks_nullable_room(conn: sqlite3.Connection) -> None:
    """SQLite can't drop NOT NULL in place: copy tasks into the new shape. Runs with foreign
    keys off so dropping the old table doesn't cascade into completions/notes."""
    notnull = {r["name"]: r["notnull"] for r in conn.execute("PRAGMA table_info(tasks)")}
    if not notnull.get("room_id"):
        return
    conn.commit()
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.execute("ALTER TABLE tasks RENAME TO tasks_old")
    conn.execute(
        """CREATE TABLE tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            room_id INTEGER REFERENCES rooms(id) ON DELETE CASCADE,
            title TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            frequency_days INTEGER,
            sort_order INTEGER NOT NULL DEFAULT 0,
            active INTEGER NOT NULL DEFAULT 1,
            assignee_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
            due_on TEXT,
            created_at TEXT NOT NULL DEFAULT ''
        )"""
    )
    conn.execute(
        "INSERT INTO tasks (id, room_id, title, description, frequency_days, sort_order, active) "
        "SELECT id, room_id, title, description, frequency_days, sort_order, active FROM tasks_old"
    )
    conn.execute("DROP TABLE tasks_old")
    conn.commit()
    conn.execute("PRAGMA foreign_keys = ON")


def init_db() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with get_conn() as conn:
        conn.executescript(SCHEMA)
        pantry_cols = _cols(conn, "pantry_items")
        if "par_level" not in pantry_cols:
            conn.execute("ALTER TABLE pantry_items ADD COLUMN par_level REAL")
        if "expires_on" not in pantry_cols:
            conn.execute("ALTER TABLE pantry_items ADD COLUMN expires_on TEXT")
        conn.execute("UPDATE rooms SET name = 'Girls Room' WHERE name = 'GIrls Room'")
        if "active" not in _cols(conn, "rooms"):
            conn.execute("ALTER TABLE rooms ADD COLUMN active INTEGER NOT NULL DEFAULT 1")
        _rebuild_tasks_nullable_room(conn)
        for col, ddl in (
            ("assignee_id", "INTEGER REFERENCES users(id) ON DELETE SET NULL"),
            ("due_on", "TEXT"),
            ("created_at", "TEXT NOT NULL DEFAULT ''"),
        ):
            if col not in _cols(conn, "tasks"):
                conn.execute(f"ALTER TABLE tasks ADD COLUMN {col} {ddl}")
        conn.execute("UPDATE tasks SET created_at = strftime('%Y-%m-%dT%H:%M:%S+00:00', 'now') WHERE created_at = ''")
        for table in ("completions", "upkeep_logs"):
            if "user_id" not in _cols(conn, table):
                conn.execute(f"ALTER TABLE {table} ADD COLUMN user_id INTEGER REFERENCES users(id) ON DELETE SET NULL")
        _migrate_medications_to_prescriptions(conn)
        _remove_duplicate_rooms(conn)


def _remove_duplicate_rooms(conn: sqlite3.Connection) -> None:
    """Rooms created before the duplicate check existed: a room with no tasks whose name matches
    another room (case-insensitive) is deleted and its slug kept as an alias of the survivor, so a
    tag written with the duplicate's URL still opens the right room."""
    dupes = conn.execute(
        """
        SELECT d.id, d.slug, k.id AS keep_id FROM rooms d
        JOIN rooms k ON k.id <> d.id AND lower(trim(k.name)) = lower(trim(d.name))
        WHERE NOT EXISTS (SELECT 1 FROM tasks t WHERE t.room_id = d.id)
          AND (EXISTS (SELECT 1 FROM tasks t WHERE t.room_id = k.id) OR k.id < d.id)
        """
    ).fetchall()
    for d in dupes:
        conn.execute("INSERT OR IGNORE INTO room_slug_aliases (slug, room_id) VALUES (?, ?)", (d["slug"], d["keep_id"]))
        conn.execute("UPDATE room_slug_aliases SET room_id = ? WHERE room_id = ?", (d["keep_id"], d["id"]))
        conn.execute("DELETE FROM rooms WHERE id = ?", (d["id"],))


def _migrate_medications_to_prescriptions(conn: sqlite3.Connection) -> None:
    """One-time copy of the old medications/med_pickups rows into children/prescriptions/pickups.
    The old tables are left in place, unused."""
    if conn.execute("SELECT COUNT(*) FROM prescriptions").fetchone()[0]:
        return
    meds = conn.execute("SELECT * FROM medications ORDER BY id").fetchall()
    if not meds:
        return
    for m in meds:
        child = conn.execute("SELECT id FROM children WHERE lower(name) = lower(?)", (m["person"],)).fetchone()
        child_id = child["id"] if child else conn.execute("INSERT INTO children (name) VALUES (?)", (m["person"],)).lastrowid
        cur = conn.execute(
            "INSERT INTO prescriptions (child_id, name, refill_after_days, days_supply, active, notes) VALUES (?, ?, ?, ?, ?, ?)",
            (child_id, m["name"], m["reorder_days"], max(30, m["reorder_days"]), m["active"], m["notes"]),
        )
        conn.execute(
            "INSERT INTO pickups (prescription_id, picked_up_on, picked_up_by) "
            "SELECT ?, picked_up_on, picked_up_by FROM med_pickups WHERE medication_id = ? ORDER BY picked_up_on, id",
            (cur.lastrowid, m["id"]),
        )


@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()
