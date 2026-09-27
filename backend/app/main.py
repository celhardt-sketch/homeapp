import asyncio
import logging
import re
import sqlite3
from difflib import SequenceMatcher
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from typing import Union

from . import auth, notify
from .db import get_conn, init_db
from .seed import seed_if_empty

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
log = logging.getLogger("uvicorn.error")


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    seed_if_empty()
    try:
        auth.ensure_admin_credentials()
    except auth.MissingPasswordError as exc:
        log.critical("%s", exc)
        raise
    reminder_task = asyncio.create_task(notify.reminder_loop())
    yield
    reminder_task.cancel()


app = FastAPI(title="Home Maintenance", lifespan=lifespan)

PUBLIC_API_PATHS = frozenset({"/api/health", "/api/login"})
SESSION_TOKEN_HEADER = "X-Session-Token"


@app.middleware("http")
async def require_login_for_api(request: Request, call_next):
    """Deny-by-default: every /api route needs a valid session token unless allow-listed.

    The role is stored on request.state for the per-route role dependencies. Household
    sessions are sliding: a renewed token is returned on every request.
    """
    path = request.url.path.rstrip("/") or "/"
    request.state.role = None
    is_api = path == "/api" or path.startswith("/api/")
    if is_api and path not in PUBLIC_API_PATHS:
        try:
            request.state.role = auth.authenticate(request.headers.get("authorization"))
        except HTTPException as exc:
            return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
    response = await call_next(request)
    if request.state.role == auth.HOUSEHOLD:
        response.headers[SESSION_TOKEN_HEADER] = auth.issue_token(auth.HOUSEHOLD)
    return response


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


class HouseholdPasswordIn(BaseModel):
    new_password: str = Field(min_length=4, max_length=200)


class PantryItemIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    category: str = Field(default="", max_length=60)
    quantity: str = Field(default="", max_length=60)
    low: bool = False
    par_level: float | None = Field(default=None, ge=0)
    expires_on: date | None = None
    updated_by: str = Field(default="", max_length=60)


class PantryItemPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    category: str | None = Field(default=None, max_length=60)
    quantity: str | None = Field(default=None, max_length=60)
    low: bool | None = None
    par_level: float | None = Field(default=None, ge=0)
    expires_on: date | None = None
    updated_by: str = Field(default="", max_length=60)


class MedicationIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    person: str = Field(min_length=1, max_length=60)
    reorder_days: int = Field(default=28, ge=1, le=365)
    notes: str = Field(default="", max_length=500)


class MedicationPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    person: str | None = Field(default=None, min_length=1, max_length=60)
    reorder_days: int | None = Field(default=None, ge=1, le=365)
    notes: str | None = Field(default=None, max_length=500)
    active: bool | None = None


class PickupIn(BaseModel):
    picked_up_on: date
    picked_up_by: str = Field(default="", max_length=60)


class UpkeepItemIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    category: str = Field(default="", max_length=60)
    interval_days: int = Field(default=90, ge=1, le=3650)
    notes: str = Field(default="", max_length=500)
    last_done_on: date | None = None


class UpkeepItemPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    category: str | None = Field(default=None, max_length=60)
    interval_days: int | None = Field(default=None, ge=1, le=3650)
    notes: str | None = Field(default=None, max_length=500)
    active: bool | None = None


class UpkeepLogIn(BaseModel):
    done_on: date
    done_by: str = Field(default="", max_length=60)
    note: str = Field(default="", max_length=500)


class ReminderSettingsIn(BaseModel):
    reminder_email: str = Field(max_length=200)


# Every /api route declares exactly one of these (enforced by tests/test_api_auth.py).
HOUSEHOLD = [Depends(auth.need_household)]  # household or admin
ADMIN = [Depends(auth.need_admin)]  # admin only -> 403 for household


# ---------- Auth ----------


@app.post("/api/login")
def login(body: LoginIn):
    role = auth.role_for_password(body.password)
    if role is None:
        raise HTTPException(401, "Incorrect password")
    return {"token": auth.issue_token(role), "role": role}


@app.get("/api/session", dependencies=HOUSEHOLD)
def session(request: Request):
    return {"role": request.state.role}


@app.post("/api/admin/password", dependencies=ADMIN)
def admin_change_password(body: PasswordChangeIn):
    if not auth.check_password(auth.ADMIN, body.current_password):
        raise HTTPException(401, "Current password is incorrect")
    auth.set_password(auth.ADMIN, body.new_password)
    return {"token": auth.issue_token(auth.ADMIN)}


@app.put("/api/admin/household-password", dependencies=ADMIN)
def admin_set_household_password(body: HouseholdPasswordIn):
    auth.set_password(auth.HOUSEHOLD, body.new_password)
    return {"ok": True}


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


@app.get("/api/rooms", dependencies=HOUSEHOLD)
def list_rooms():
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM rooms ORDER BY sort_order, id").fetchall()
        return [serialize_room(conn, r, with_tasks=False) for r in rows]


@app.post("/api/rooms", status_code=201, dependencies=HOUSEHOLD)
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


@app.get("/api/rooms/{slug}", dependencies=HOUSEHOLD)
def get_room(slug: str):
    with get_conn() as conn:
        r = conn.execute("SELECT * FROM rooms WHERE slug = ?", (slug,)).fetchone()
        if not r:
            raise HTTPException(404, "Room not found")
        return serialize_room(conn, r, with_tasks=True)


@app.patch("/api/rooms/{room_id}", dependencies=HOUSEHOLD)
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


@app.post("/api/tasks", status_code=201, dependencies=HOUSEHOLD)
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


@app.patch("/api/tasks/{task_id}", dependencies=HOUSEHOLD)
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


@app.delete("/api/tasks/{task_id}", status_code=204, dependencies=HOUSEHOLD)
def delete_task(task_id: int):
    with get_conn() as conn:
        fetch_task_or_404(conn, task_id)
        conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))


@app.post("/api/tasks/{task_id}/complete", status_code=201, dependencies=HOUSEHOLD)
def complete_task(task_id: int, body: CompleteIn):
    with get_conn() as conn:
        fetch_task_or_404(conn, task_id)
        conn.execute(
            "INSERT INTO completions (task_id, completed_by, completed_at) VALUES (?, ?, ?)",
            (task_id, body.completed_by.strip(), now_iso()),
        )
        return serialize_task(conn, fetch_task_or_404(conn, task_id))


@app.delete("/api/completions/{completion_id}", status_code=204, dependencies=HOUSEHOLD)
def undo_completion(completion_id: int):
    with get_conn() as conn:
        c = conn.execute("SELECT * FROM completions WHERE id = ?", (completion_id,)).fetchone()
        if not c:
            raise HTTPException(404, "Completion not found")
        conn.execute("DELETE FROM completions WHERE id = ?", (completion_id,))


@app.get("/api/tasks/{task_id}/history", dependencies=HOUSEHOLD)
def task_history(task_id: int, limit: int = 50):
    with get_conn() as conn:
        fetch_task_or_404(conn, task_id)
        rows = conn.execute(
            "SELECT id, completed_by, completed_at FROM completions WHERE task_id = ? ORDER BY completed_at DESC LIMIT ?",
            (task_id, limit),
        ).fetchall()
        return [dict(r) for r in rows]


# ---------- Notes ----------


@app.post("/api/tasks/{task_id}/notes", status_code=201, dependencies=HOUSEHOLD)
def add_note(task_id: int, body: NoteIn):
    with get_conn() as conn:
        fetch_task_or_404(conn, task_id)
        cur = conn.execute(
            "INSERT INTO notes (task_id, author, body, needs_purchase, created_at) VALUES (?, ?, ?, ?, ?)",
            (task_id, body.author.strip(), body.body.strip(), int(body.needs_purchase), now_iso()),
        )
        n = conn.execute("SELECT * FROM notes WHERE id = ?", (cur.lastrowid,)).fetchone()
        return serialize_note(n)


@app.patch("/api/notes/{note_id}", dependencies=HOUSEHOLD)
def update_note(note_id: int, body: NotePatch):
    with get_conn() as conn:
        n = conn.execute("SELECT * FROM notes WHERE id = ?", (note_id,)).fetchone()
        if not n:
            raise HTTPException(404, "Note not found")
        conn.execute("UPDATE notes SET resolved = ? WHERE id = ?", (int(body.resolved), note_id))
        return serialize_note(conn.execute("SELECT * FROM notes WHERE id = ?", (note_id,)).fetchone())


@app.get("/api/shopping", dependencies=HOUSEHOLD)
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


@app.get("/api/activity", dependencies=ADMIN)
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


# ---------- Pantry ----------


_NUMBER_RE = re.compile(r"-?\d+(?:\.\d+)?")


def quantity_number(quantity: str) -> float | None:
    """Leading number in a free-text quantity like '2 bags' or '0.5'; None if there is none."""
    m = _NUMBER_RE.search(quantity or "")
    return float(m.group()) if m else None


def pantry_below_par(r: sqlite3.Row) -> bool:
    if r["par_level"] is None:
        return False
    qty = quantity_number(r["quantity"])
    return qty is not None and qty <= r["par_level"]


def pantry_days_to_expiry(r: sqlite3.Row) -> int | None:
    if not r["expires_on"]:
        return None
    return (date.fromisoformat(r["expires_on"]) - date.today()).days


def serialize_pantry(r: sqlite3.Row) -> dict:
    below_par = pantry_below_par(r)
    return {
        **dict(r),
        "low": bool(r["low"]) or below_par,
        "low_flag": bool(r["low"]),
        "below_par": below_par,
        "days_to_expiry": pantry_days_to_expiry(r),
    }


def _normalize_name(name: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", "", name.lower()).strip()


def find_pantry_duplicate(conn: sqlite3.Connection, name: str) -> sqlite3.Row | None:
    """Existing row whose name matches case-insensitively, as a substring, or fuzzily."""
    target = _normalize_name(name)
    if not target:
        return None
    best, best_score = None, 0.0
    for r in conn.execute("SELECT * FROM pantry_items").fetchall():
        existing = _normalize_name(r["name"])
        if not existing:
            continue
        if existing == target:
            return r
        # names that differ in a number ("size 4" vs "size 5") are different products
        if _NUMBER_RE.findall(existing) != _NUMBER_RE.findall(target):
            continue
        shorter, longer = sorted((existing, target), key=len)
        if len(shorter) >= 3 and shorter in longer:
            return r
        score = SequenceMatcher(None, existing, target).ratio()
        if score > best_score:
            best, best_score = r, score
    return best if best_score >= 0.88 else None


@app.get("/api/pantry", dependencies=HOUSEHOLD)
def list_pantry():
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM pantry_items ORDER BY category, name").fetchall()
        return [serialize_pantry(r) for r in rows]


def _insert_pantry_item(conn: sqlite3.Connection, body: PantryItemIn) -> dict:
    """Insert unless a near-duplicate exists, in which case return that row flagged `duplicate`."""
    existing = find_pantry_duplicate(conn, body.name)
    if existing:
        return {**serialize_pantry(existing), "duplicate": True, "requested_name": body.name.strip()}
    cur = conn.execute(
        "INSERT INTO pantry_items (name, category, quantity, low, par_level, expires_on, updated_by, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            body.name.strip(),
            body.category.strip(),
            body.quantity.strip(),
            int(body.low),
            body.par_level,
            body.expires_on.isoformat() if body.expires_on else None,
            body.updated_by.strip(),
            now_iso(),
        ),
    )
    row = conn.execute("SELECT * FROM pantry_items WHERE id = ?", (cur.lastrowid,)).fetchone()
    return {**serialize_pantry(row), "duplicate": False}


@app.post("/api/pantry", status_code=201, dependencies=HOUSEHOLD)
def create_pantry_item(body: Union[PantryItemIn, list[PantryItemIn]]):
    """Accepts one item or a list; a list is inserted in a single transaction and returns a list."""
    with get_conn() as conn:
        if isinstance(body, list):
            if not body:
                raise HTTPException(422, "Empty list")
            if len(body) > 200:
                raise HTTPException(422, "Too many items in one request")
            return [_insert_pantry_item(conn, item) for item in body]
        return _insert_pantry_item(conn, body)


@app.patch("/api/pantry/{item_id}", dependencies=HOUSEHOLD)
def update_pantry_item(item_id: int, body: PantryItemPatch):
    with get_conn() as conn:
        if not conn.execute("SELECT 1 FROM pantry_items WHERE id = ?", (item_id,)).fetchone():
            raise HTTPException(404, "Item not found")
        fields = body.model_dump(exclude_unset=True, exclude={"updated_by"})
        if "low" in fields:
            fields["low"] = int(fields["low"])
        if fields.get("expires_on") is not None:
            fields["expires_on"] = fields["expires_on"].isoformat()
        for k in ("name", "category", "quantity"):
            if k in fields and fields[k] is not None:
                fields[k] = fields[k].strip()
        fields["updated_by"] = body.updated_by.strip()
        fields["updated_at"] = now_iso()
        sets = ", ".join(f"{k} = ?" for k in fields)
        conn.execute(f"UPDATE pantry_items SET {sets} WHERE id = ?", (*fields.values(), item_id))
        return serialize_pantry(conn.execute("SELECT * FROM pantry_items WHERE id = ?", (item_id,)).fetchone())


@app.delete("/api/pantry/{item_id}", status_code=204, dependencies=HOUSEHOLD)
def delete_pantry_item(item_id: int):
    with get_conn() as conn:
        if not conn.execute("SELECT 1 FROM pantry_items WHERE id = ?", (item_id,)).fetchone():
            raise HTTPException(404, "Item not found")
        conn.execute("DELETE FROM pantry_items WHERE id = ?", (item_id,))


# ---------- Medications ----------


def serialize_medication(conn: sqlite3.Connection, m: sqlite3.Row) -> dict:
    last = conn.execute(
        "SELECT * FROM med_pickups WHERE medication_id = ? ORDER BY picked_up_on DESC, id DESC LIMIT 1",
        (m["id"],),
    ).fetchone()
    reorder_on = None
    days_left = None
    status = "none"
    if last:
        reorder_date = date.fromisoformat(last["picked_up_on"]) + timedelta(days=m["reorder_days"])
        reorder_on = reorder_date.isoformat()
        days_left = (reorder_date - date.today()).days
        status = "due" if days_left <= 0 else "soon" if days_left <= 5 else "ok"
    return {
        "id": m["id"],
        "name": m["name"],
        "person": m["person"],
        "reorder_days": m["reorder_days"],
        "notes": m["notes"],
        "active": bool(m["active"]),
        "last_picked_up_on": last["picked_up_on"] if last else None,
        "last_picked_up_by": last["picked_up_by"] if last else None,
        "last_pickup_id": last["id"] if last else None,
        "reminder_sent_at": last["reminder_sent_at"] if last else None,
        "reorder_on": reorder_on,
        "days_left": days_left,
        "status": status,
    }


def fetch_med_or_404(conn: sqlite3.Connection, med_id: int) -> sqlite3.Row:
    m = conn.execute("SELECT * FROM medications WHERE id = ?", (med_id,)).fetchone()
    if not m:
        raise HTTPException(404, "Medication not found")
    return m


@app.get("/api/medications", dependencies=ADMIN)
def list_medications(include_inactive: bool = False):
    with get_conn() as conn:
        where = "" if include_inactive else "WHERE active = 1"
        rows = conn.execute(f"SELECT * FROM medications {where} ORDER BY person, name").fetchall()
        return [serialize_medication(conn, m) for m in rows]


@app.post("/api/medications", status_code=201, dependencies=ADMIN)
def create_medication(body: MedicationIn):
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO medications (name, person, reorder_days, notes) VALUES (?, ?, ?, ?)",
            (body.name.strip(), body.person.strip(), body.reorder_days, body.notes.strip()),
        )
        return serialize_medication(conn, fetch_med_or_404(conn, cur.lastrowid))


@app.patch("/api/medications/{med_id}", dependencies=ADMIN)
def update_medication(med_id: int, body: MedicationPatch):
    with get_conn() as conn:
        fetch_med_or_404(conn, med_id)
        fields = body.model_dump(exclude_unset=True)
        if "active" in fields:
            fields["active"] = int(fields["active"])
        for k in ("name", "person", "notes"):
            if k in fields and fields[k] is not None:
                fields[k] = fields[k].strip()
        if fields:
            sets = ", ".join(f"{k} = ?" for k in fields)
            conn.execute(f"UPDATE medications SET {sets} WHERE id = ?", (*fields.values(), med_id))
        return serialize_medication(conn, fetch_med_or_404(conn, med_id))


@app.delete("/api/medications/{med_id}", status_code=204, dependencies=ADMIN)
def delete_medication(med_id: int):
    with get_conn() as conn:
        fetch_med_or_404(conn, med_id)
        conn.execute("DELETE FROM medications WHERE id = ?", (med_id,))


@app.post("/api/medications/{med_id}/pickups", status_code=201, dependencies=ADMIN)
def log_pickup(med_id: int, body: PickupIn):
    with get_conn() as conn:
        fetch_med_or_404(conn, med_id)
        conn.execute(
            "INSERT INTO med_pickups (medication_id, picked_up_on, picked_up_by) VALUES (?, ?, ?)",
            (med_id, body.picked_up_on.isoformat(), body.picked_up_by.strip()),
        )
        return serialize_medication(conn, fetch_med_or_404(conn, med_id))


@app.get("/api/medications/{med_id}/pickups", dependencies=ADMIN)
def pickup_history(med_id: int, limit: int = 50):
    with get_conn() as conn:
        fetch_med_or_404(conn, med_id)
        rows = conn.execute(
            "SELECT * FROM med_pickups WHERE medication_id = ? ORDER BY picked_up_on DESC, id DESC LIMIT ?",
            (med_id, limit),
        ).fetchall()
        return [dict(r) for r in rows]


@app.delete("/api/pickups/{pickup_id}", status_code=204, dependencies=ADMIN)
def delete_pickup(pickup_id: int):
    with get_conn() as conn:
        if not conn.execute("SELECT 1 FROM med_pickups WHERE id = ?", (pickup_id,)).fetchone():
            raise HTTPException(404, "Pickup not found")
        conn.execute("DELETE FROM med_pickups WHERE id = ?", (pickup_id,))


# ---------- Home upkeep (house-wide recurring maintenance) ----------


def serialize_upkeep(conn: sqlite3.Connection, i: sqlite3.Row) -> dict:
    last = conn.execute(
        "SELECT * FROM upkeep_logs WHERE item_id = ? ORDER BY done_on DESC, id DESC LIMIT 1",
        (i["id"],),
    ).fetchone()
    due_on = None
    days_left = None
    status = "none"
    if last:
        due_date = date.fromisoformat(last["done_on"]) + timedelta(days=i["interval_days"])
        due_on = due_date.isoformat()
        days_left = (due_date - date.today()).days
        soon_window = max(3, i["interval_days"] // 10)
        status = "due" if days_left <= 0 else "soon" if days_left <= soon_window else "ok"
    return {
        "id": i["id"],
        "name": i["name"],
        "category": i["category"],
        "interval_days": i["interval_days"],
        "notes": i["notes"],
        "active": bool(i["active"]),
        "last_done_on": last["done_on"] if last else None,
        "last_done_by": last["done_by"] if last else None,
        "due_on": due_on,
        "days_left": days_left,
        "status": status,
    }


def fetch_upkeep_or_404(conn: sqlite3.Connection, item_id: int) -> sqlite3.Row:
    i = conn.execute("SELECT * FROM upkeep_items WHERE id = ?", (item_id,)).fetchone()
    if not i:
        raise HTTPException(404, "Item not found")
    return i


@app.get("/api/upkeep", dependencies=HOUSEHOLD)
def list_upkeep(include_inactive: bool = False):
    with get_conn() as conn:
        where = "" if include_inactive else "WHERE active = 1"
        rows = conn.execute(f"SELECT * FROM upkeep_items {where} ORDER BY category, name").fetchall()
        return [serialize_upkeep(conn, i) for i in rows]


@app.post("/api/upkeep", status_code=201, dependencies=HOUSEHOLD)
def create_upkeep(body: UpkeepItemIn):
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO upkeep_items (name, category, interval_days, notes) VALUES (?, ?, ?, ?)",
            (body.name.strip(), body.category.strip(), body.interval_days, body.notes.strip()),
        )
        if body.last_done_on:
            conn.execute(
                "INSERT INTO upkeep_logs (item_id, done_on) VALUES (?, ?)",
                (cur.lastrowid, body.last_done_on.isoformat()),
            )
        return serialize_upkeep(conn, fetch_upkeep_or_404(conn, cur.lastrowid))


@app.patch("/api/upkeep/{item_id}", dependencies=HOUSEHOLD)
def update_upkeep(item_id: int, body: UpkeepItemPatch):
    with get_conn() as conn:
        fetch_upkeep_or_404(conn, item_id)
        fields = body.model_dump(exclude_unset=True)
        if "active" in fields:
            fields["active"] = int(fields["active"])
        for k in ("name", "category", "notes"):
            if k in fields and fields[k] is not None:
                fields[k] = fields[k].strip()
        if fields:
            sets = ", ".join(f"{k} = ?" for k in fields)
            conn.execute(f"UPDATE upkeep_items SET {sets} WHERE id = ?", (*fields.values(), item_id))
        return serialize_upkeep(conn, fetch_upkeep_or_404(conn, item_id))


@app.delete("/api/upkeep/{item_id}", status_code=204, dependencies=HOUSEHOLD)
def delete_upkeep(item_id: int):
    with get_conn() as conn:
        fetch_upkeep_or_404(conn, item_id)
        conn.execute("DELETE FROM upkeep_items WHERE id = ?", (item_id,))


@app.post("/api/upkeep/{item_id}/logs", status_code=201, dependencies=HOUSEHOLD)
def log_upkeep(item_id: int, body: UpkeepLogIn):
    with get_conn() as conn:
        fetch_upkeep_or_404(conn, item_id)
        conn.execute(
            "INSERT INTO upkeep_logs (item_id, done_on, done_by, note) VALUES (?, ?, ?, ?)",
            (item_id, body.done_on.isoformat(), body.done_by.strip(), body.note.strip()),
        )
        return serialize_upkeep(conn, fetch_upkeep_or_404(conn, item_id))


@app.get("/api/upkeep/{item_id}/logs", dependencies=HOUSEHOLD)
def upkeep_history(item_id: int, limit: int = 50):
    with get_conn() as conn:
        fetch_upkeep_or_404(conn, item_id)
        rows = conn.execute(
            "SELECT * FROM upkeep_logs WHERE item_id = ? ORDER BY done_on DESC, id DESC LIMIT ?",
            (item_id, limit),
        ).fetchall()
        return [dict(r) for r in rows]


@app.delete("/api/upkeep-logs/{log_id}", status_code=204, dependencies=HOUSEHOLD)
def delete_upkeep_log(log_id: int):
    with get_conn() as conn:
        if not conn.execute("SELECT 1 FROM upkeep_logs WHERE id = ?", (log_id,)).fetchone():
            raise HTTPException(404, "Log entry not found")
        conn.execute("DELETE FROM upkeep_logs WHERE id = ?", (log_id,))


# ---------- Reminder settings (admin) ----------


@app.get("/api/admin/reminders", dependencies=ADMIN)
def get_reminder_settings():
    return {
        "reminder_email": notify.get_reminder_email(),
        "email_configured": notify.email_configured(),
        "due": notify.due_pickups(),
        "due_upkeep": notify.due_upkeep(),
    }


@app.put("/api/admin/reminders", dependencies=ADMIN)
def set_reminder_settings(body: ReminderSettingsIn):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO settings (key, value) VALUES ('reminder_email', ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (body.reminder_email.strip(),),
        )
    return get_reminder_settings()


@app.post("/api/admin/reminders/test", dependencies=ADMIN)
def send_test_reminder():
    if not notify.email_configured():
        raise HTTPException(400, "Email isn't configured on the server (set RESEND_API_KEY or SMTP_HOST)")
    to = notify.get_reminder_email()
    if not to:
        raise HTTPException(400, "Enter a reminder email address first")
    try:
        notify.send_email(to, "Home Maintenance test email", "Reminder emails are working.")
    except Exception as e:
        raise HTTPException(502, f"Sending failed: {e}")
    return {"ok": True}


# ---------- Status summary ----------
# Household-readable. Must never include medication/pickup data (see tests).


def task_days_until_due(t: dict) -> int | None:
    """Days until a recurring task is due (negative = overdue). None for one-off tasks."""
    if t["frequency_days"] is None:
        return None
    if t["last_completed_at"] is None:
        return 0
    last = datetime.fromisoformat(t["last_completed_at"]).date()
    return (last + timedelta(days=t["frequency_days"]) - date.today()).days


def _status_task(t: dict, room: sqlite3.Row, days: int) -> dict:
    return {
        "id": t["id"],
        "title": t["title"],
        "room_slug": room["slug"],
        "room_name": room["name"],
        "days_until_due": days,
        "last_completed_at": t["last_completed_at"],
    }


@app.get("/api/status", dependencies=HOUSEHOLD)
def status_summary():
    overdue, due_today, due_week = [], [], []
    with get_conn() as conn:
        rooms = {r["id"]: r for r in conn.execute("SELECT * FROM rooms ORDER BY sort_order, id").fetchall()}
        tasks = conn.execute("SELECT * FROM tasks WHERE active = 1 ORDER BY sort_order, id").fetchall()
        for row in tasks:
            t = serialize_task(conn, row)
            days = task_days_until_due(t)
            if days is None:
                continue
            entry = _status_task(t, rooms[row["room_id"]], days)
            if days < 0:
                overdue.append(entry)
            elif days == 0:
                due_today.append(entry)
            elif days <= 7:
                due_week.append(entry)
        upkeep = [
            serialize_upkeep(conn, i)
            for i in conn.execute("SELECT * FROM upkeep_items WHERE active = 1 ORDER BY name").fetchall()
        ]
        pantry = [serialize_pantry(r) for r in conn.execute("SELECT * FROM pantry_items ORDER BY name").fetchall()]
    return {
        "generated_on": date.today().isoformat(),
        "tasks_overdue": overdue,
        "tasks_due_today": due_today,
        "tasks_due_within_7_days": due_week,
        "upkeep_due": [u for u in upkeep if u["status"] == "due"],
        "pantry_below_par": [p for p in pantry if p["below_par"]],
        "pantry_expiring_within_7_days": [
            p for p in pantry if p["days_to_expiry"] is not None and p["days_to_expiry"] <= 7
        ],
    }


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
