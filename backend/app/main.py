import re
import sqlite3
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import auth
from .db import get_conn, init_db
from .seed import seed_if_empty

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    seed_if_empty()
    auth.ensure_admin_credentials()
    yield


app = FastAPI(title="Home Maintenance", lifespan=lifespan)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "room"


# ---------- Schemas ----------


class RoomIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    slug: str | None = Field(default=None, max_length=80)
    icon: str = "home"


class RoomPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    slug: str | None = Field(default=None, min_length=1, max_length=80)
    icon: str | None = None
    sort_order: int | None = None


class TaskIn(BaseModel):
    room_id: int
    title: str = Field(min_length=1, max_length=120)
    description: str = ""
    frequency_days: int | None = Field(default=None, ge=1)


class TaskPatch(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = None
    frequency_days: int | None = Field(default=None, ge=0)
    sort_order: int | None = None
    active: bool | None = None


class CompleteIn(BaseModel):
    completed_by: str = Field(min_length=1, max_length=60)


class NoteIn(BaseModel):
    author: str = Field(min_length=1, max_length=60)
    body: str = Field(min_length=1, max_length=1000)
    needs_purchase: bool = False


class NotePatch(BaseModel):
    resolved: bool


class LoginIn(BaseModel):
    password: str = Field(min_length=1, max_length=200)


class PasswordChangeIn(BaseModel):
    current_password: str = Field(min_length=1, max_length=200)
    new_password: str = Field(min_length=4, max_length=200)


ADMIN = [Depends(auth.require_admin)]


# ---------- Admin auth ----------


@app.post("/api/admin/login")
def admin_login(body: LoginIn):
    if not auth.check_password(body.password):
        raise HTTPException(401, "Incorrect password")
    return {"token": auth.issue_token()}


@app.get("/api/admin/me", dependencies=ADMIN)
def admin_me():
    return {"ok": True}


@app.post("/api/admin/password", dependencies=ADMIN)
def admin_change_password(body: PasswordChangeIn):
    if not auth.check_password(body.current_password):
        raise HTTPException(401, "Current password is incorrect")
    auth.set_password(body.new_password)
    return {"token": auth.issue_token()}


# ---------- Helpers ----------


def task_status(last_completed_at: str | None, frequency_days: int | None) -> str:
    if frequency_days is None:
        return "ok"
    if last_completed_at is None:
        return "due"
    last = datetime.fromisoformat(last_completed_at)
    age_days = (datetime.now(timezone.utc) - last).total_seconds() / 86400
    if age_days >= frequency_days * 1.5:
        return "overdue"
    if age_days >= frequency_days:
        return "due"
    return "ok"


def serialize_task(conn: sqlite3.Connection, t: sqlite3.Row) -> dict:
    last = conn.execute(
        "SELECT completed_by, completed_at FROM completions WHERE task_id = ? ORDER BY completed_at DESC LIMIT 1",
        (t["id"],),
    ).fetchone()
    notes = conn.execute(
        "SELECT * FROM notes WHERE task_id = ? AND resolved = 0 ORDER BY created_at DESC",
        (t["id"],),
    ).fetchall()
    last_at = last["completed_at"] if last else None
    return {
        "id": t["id"],
        "room_id": t["room_id"],
        "title": t["title"],
        "description": t["description"],
        "frequency_days": t["frequency_days"],
        "sort_order": t["sort_order"],
        "active": bool(t["active"]),
        "last_completed_at": last_at,
        "last_completed_by": last["completed_by"] if last else None,
        "status": task_status(last_at, t["frequency_days"]),
        "notes": [serialize_note(n) for n in notes],
    }


def serialize_note(n: sqlite3.Row) -> dict:
    return {
        "id": n["id"],
        "task_id": n["task_id"],
        "author": n["author"],
        "body": n["body"],
        "needs_purchase": bool(n["needs_purchase"]),
        "resolved": bool(n["resolved"]),
        "created_at": n["created_at"],
    }


def serialize_room(conn: sqlite3.Connection, r: sqlite3.Row, with_tasks: bool) -> dict:
    tasks = conn.execute(
        "SELECT * FROM tasks WHERE room_id = ? AND active = 1 ORDER BY sort_order, id",
        (r["id"],),
    ).fetchall()
    serialized = [serialize_task(conn, t) for t in tasks]
    out = {
        "id": r["id"],
        "slug": r["slug"],
        "name": r["name"],
        "icon": r["icon"],
        "sort_order": r["sort_order"],
        "task_count": len(serialized),
        "due_count": sum(1 for t in serialized if t["status"] != "ok"),
        "note_count": sum(len(t["notes"]) for t in serialized),
    }
    if with_tasks:
        out["tasks"] = serialized
    return out


def fetch_room_or_404(conn: sqlite3.Connection, room_id: int) -> sqlite3.Row:
    r = conn.execute("SELECT * FROM rooms WHERE id = ?", (room_id,)).fetchone()
    if not r:
        raise HTTPException(404, "Room not found")
    return r


def fetch_task_or_404(conn: sqlite3.Connection, task_id: int) -> sqlite3.Row:
    t = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    if not t:
        raise HTTPException(404, "Task not found")
    return t


# ---------- Rooms ----------


@app.get("/api/rooms")
def list_rooms():
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM rooms ORDER BY sort_order, id").fetchall()
        return [serialize_room(conn, r, with_tasks=False) for r in rows]


@app.post("/api/rooms", status_code=201, dependencies=ADMIN)
def create_room(body: RoomIn):
    slug = slugify(body.slug or body.name)
    with get_conn() as conn:
        if conn.execute("SELECT 1 FROM rooms WHERE slug = ?", (slug,)).fetchone():
            raise HTTPException(409, f"A room with the tag id '{slug}' already exists")
        max_order = conn.execute("SELECT COALESCE(MAX(sort_order), -1) FROM rooms").fetchone()[0]
        cur = conn.execute(
            "INSERT INTO rooms (slug, name, icon, sort_order) VALUES (?, ?, ?, ?)",
            (slug, body.name.strip(), body.icon, max_order + 1),
        )
        return serialize_room(conn, fetch_room_or_404(conn, cur.lastrowid), with_tasks=True)


@app.get("/api/rooms/{slug}")
def get_room(slug: str):
    with get_conn() as conn:
        r = conn.execute("SELECT * FROM rooms WHERE slug = ?", (slug,)).fetchone()
        if not r:
            raise HTTPException(404, "Room not found")
        return serialize_room(conn, r, with_tasks=True)


@app.patch("/api/rooms/{room_id}", dependencies=ADMIN)
def update_room(room_id: int, body: RoomPatch):
    with get_conn() as conn:
        fetch_room_or_404(conn, room_id)
        fields = body.model_dump(exclude_none=True)
        if "slug" in fields:
            fields["slug"] = slugify(fields["slug"])
            dup = conn.execute(
                "SELECT 1 FROM rooms WHERE slug = ? AND id != ?", (fields["slug"], room_id)
            ).fetchone()
            if dup:
                raise HTTPException(409, "That tag id is already in use")
        if "name" in fields:
            fields["name"] = fields["name"].strip()
        if fields:
            sets = ", ".join(f"{k} = ?" for k in fields)
            conn.execute(f"UPDATE rooms SET {sets} WHERE id = ?", (*fields.values(), room_id))
        return serialize_room(conn, fetch_room_or_404(conn, room_id), with_tasks=True)


@app.delete("/api/rooms/{room_id}", status_code=204, dependencies=ADMIN)
def delete_room(room_id: int):
    with get_conn() as conn:
        fetch_room_or_404(conn, room_id)
        conn.execute("DELETE FROM rooms WHERE id = ?", (room_id,))


# ---------- Tasks ----------


@app.post("/api/tasks", status_code=201, dependencies=ADMIN)
def create_task(body: TaskIn):
    with get_conn() as conn:
        fetch_room_or_404(conn, body.room_id)
        max_order = conn.execute(
            "SELECT COALESCE(MAX(sort_order), -1) FROM tasks WHERE room_id = ?", (body.room_id,)
        ).fetchone()[0]
        cur = conn.execute(
            "INSERT INTO tasks (room_id, title, description, frequency_days, sort_order) VALUES (?, ?, ?, ?, ?)",
            (body.room_id, body.title.strip(), body.description.strip(), body.frequency_days, max_order + 1),
        )
        return serialize_task(conn, fetch_task_or_404(conn, cur.lastrowid))


@app.patch("/api/tasks/{task_id}", dependencies=ADMIN)
def update_task(task_id: int, body: TaskPatch):
    with get_conn() as conn:
        fetch_task_or_404(conn, task_id)
        fields = body.model_dump(exclude_unset=True)
        if "frequency_days" in fields and not fields["frequency_days"]:
            fields["frequency_days"] = None
        if "active" in fields:
            fields["active"] = int(fields["active"])
        if "title" in fields and fields["title"]:
            fields["title"] = fields["title"].strip()
        if fields:
            sets = ", ".join(f"{k} = ?" for k in fields)
            conn.execute(f"UPDATE tasks SET {sets} WHERE id = ?", (*fields.values(), task_id))
        return serialize_task(conn, fetch_task_or_404(conn, task_id))


@app.delete("/api/tasks/{task_id}", status_code=204, dependencies=ADMIN)
def delete_task(task_id: int):
    with get_conn() as conn:
        fetch_task_or_404(conn, task_id)
        conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))


@app.post("/api/tasks/{task_id}/complete", status_code=201)
def complete_task(task_id: int, body: CompleteIn):
    with get_conn() as conn:
        fetch_task_or_404(conn, task_id)
        conn.execute(
            "INSERT INTO completions (task_id, completed_by, completed_at) VALUES (?, ?, ?)",
            (task_id, body.completed_by.strip(), now_iso()),
        )
        return serialize_task(conn, fetch_task_or_404(conn, task_id))


@app.delete("/api/completions/{completion_id}", status_code=204)
def undo_completion(completion_id: int):
    with get_conn() as conn:
        c = conn.execute("SELECT * FROM completions WHERE id = ?", (completion_id,)).fetchone()
        if not c:
            raise HTTPException(404, "Completion not found")
        conn.execute("DELETE FROM completions WHERE id = ?", (completion_id,))


@app.get("/api/tasks/{task_id}/history")
def task_history(task_id: int, limit: int = 50):
    with get_conn() as conn:
        fetch_task_or_404(conn, task_id)
        rows = conn.execute(
            "SELECT id, completed_by, completed_at FROM completions WHERE task_id = ? ORDER BY completed_at DESC LIMIT ?",
            (task_id, limit),
        ).fetchall()
        return [dict(r) for r in rows]


# ---------- Notes ----------


@app.post("/api/tasks/{task_id}/notes", status_code=201)
def add_note(task_id: int, body: NoteIn):
    with get_conn() as conn:
        fetch_task_or_404(conn, task_id)
        cur = conn.execute(
            "INSERT INTO notes (task_id, author, body, needs_purchase, created_at) VALUES (?, ?, ?, ?, ?)",
            (task_id, body.author.strip(), body.body.strip(), int(body.needs_purchase), now_iso()),
        )
        n = conn.execute("SELECT * FROM notes WHERE id = ?", (cur.lastrowid,)).fetchone()
        return serialize_note(n)


@app.patch("/api/notes/{note_id}")
def update_note(note_id: int, body: NotePatch):
    with get_conn() as conn:
        n = conn.execute("SELECT * FROM notes WHERE id = ?", (note_id,)).fetchone()
        if not n:
            raise HTTPException(404, "Note not found")
        conn.execute("UPDATE notes SET resolved = ? WHERE id = ?", (int(body.resolved), note_id))
        return serialize_note(conn.execute("SELECT * FROM notes WHERE id = ?", (note_id,)).fetchone())


@app.get("/api/shopping")
def shopping_list():
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT n.*, t.title AS task_title, r.name AS room_name, r.slug AS room_slug
            FROM notes n
            JOIN tasks t ON t.id = n.task_id
            JOIN rooms r ON r.id = t.room_id
            WHERE n.needs_purchase = 1 AND n.resolved = 0
            ORDER BY n.created_at DESC
            """
        ).fetchall()
        return [
            {**serialize_note(r), "task_title": r["task_title"], "room_name": r["room_name"], "room_slug": r["room_slug"]}
            for r in rows
        ]


@app.get("/api/activity")
def recent_activity(limit: int = 30):
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT c.id, c.completed_by, c.completed_at, t.title AS task_title, r.name AS room_name, r.slug AS room_slug
            FROM completions c
            JOIN tasks t ON t.id = c.task_id
            JOIN rooms r ON r.id = t.room_id
            ORDER BY c.completed_at DESC LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]


@app.get("/api/health")
def health():
    return {"ok": True}


# ---------- Static frontend (production) ----------

if STATIC_DIR.is_dir():
    app.mount("/assets", StaticFiles(directory=STATIC_DIR / "assets"), name="assets")

    @app.get("/{full_path:path}")
    def spa(full_path: str):
        candidate = STATIC_DIR / full_path
        if full_path and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(STATIC_DIR / "index.html")
