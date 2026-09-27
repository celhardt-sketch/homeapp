import asyncio
import logging
import re
import sqlite3
from dataclasses import dataclass
from difflib import SequenceMatcher
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from fastapi import Depends, FastAPI, Form, HTTPException, Request, Response
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from html import escape
from pydantic import BaseModel, Field
from typing import Literal, Union

from . import auth, kids, notify, oauth, refills
from .db import get_conn, init_db
from .matching import similarity
from .seed import seed_if_empty

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
log = logging.getLogger("uvicorn.error")


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    seed_if_empty()
    try:
        auth.ensure_users()
    except auth.MissingPasswordError as exc:
        log.critical("%s", exc)
        raise
    reminder_task = asyncio.create_task(notify.reminder_loop())
    async with mcp_http.lifespan(mcp_http):
        yield
    reminder_task.cancel()


app = FastAPI(title="Home Maintenance", lifespan=lifespan)

PUBLIC_API_PATHS = frozenset({"/api/health", "/api/login", "/api/login/names"})
SESSION_TOKEN_HEADER = "X-Session-Token"


@app.middleware("http")
async def require_login_for_api(request: Request, call_next):
    """Deny-by-default: every /api route needs a valid session token unless allow-listed.

    The role and user are stored on request.state for the per-route role dependencies.
    Member sessions are sliding: a renewed token is returned on every request.
    """
    path = request.url.path.rstrip("/") or "/"
    request.state.role = None
    request.state.user = None
    is_api = path == "/api" or path.startswith("/api/")
    if is_api and path not in PUBLIC_API_PATHS:
        try:
            request.state.role, request.state.user = auth.authenticate(request.headers.get("authorization"))
        except HTTPException as exc:
            return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
    response = await call_next(request)
    if request.state.role == auth.MEMBER and request.state.user is not None:
        response.headers[SESSION_TOKEN_HEADER] = auth.issue_token(request.state.user)
    return response


@dataclass(frozen=True)
class Actor:
    """Who is making a request: a named user (admin/member) or the connector (Claude)."""

    role: str
    user_id: int | None = None
    name: str = ""

    @property
    def can_assign(self) -> bool:
        return self.role in (auth.ADMIN, auth.CONNECTOR)

    def attribution(self, fallback: str = "") -> str:
        return self.name or fallback


CONNECTOR_ACTOR = Actor(role=auth.CONNECTOR, name="Claude")


def current_actor(request: Request) -> Actor:
    u = request.state.user
    if u is None:
        return CONNECTOR_ACTOR
    return Actor(role=request.state.role, user_id=u["id"], name=u["name"])


ACTOR = Depends(current_actor)


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
    sort_order: int | None = None


class RoomPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    slug: str | None = Field(default=None, min_length=1, max_length=80)
    icon: str | None = None
    sort_order: int | None = None
    active: bool | None = None


class TaskIn(BaseModel):
    room_id: int | None = None  # None = a standalone errand
    title: str = Field(min_length=1, max_length=120)
    description: str = ""
    frequency_days: int | None = Field(default=None, ge=1)
    assignee_id: int | None = None
    due_on: date | None = None


class TaskPatch(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = None
    frequency_days: int | None = Field(default=None, ge=0)
    sort_order: int | None = None
    active: bool | None = None
    assignee_id: int | None = None
    due_on: date | None = None


class CompleteIn(BaseModel):
    # Only used for the connector; a logged-in user's completion is attributed to them.
    completed_by: str = Field(default="", max_length=60)


class ShoppingItemIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    notes: str = Field(default="", max_length=500)
    assignee_id: int | None = None
    due_on: date | None = None


class ShoppingItemPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    notes: str | None = Field(default=None, max_length=500)
    assignee_id: int | None = None
    due_on: date | None = None
    bought: bool | None = None


class NoteIn(BaseModel):
    author: str = Field(min_length=1, max_length=60)
    body: str = Field(min_length=1, max_length=1000)
    needs_purchase: bool = False


class NotePatch(BaseModel):
    resolved: bool


class LoginIn(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    password: str = Field(min_length=1, max_length=200)


class PasswordChangeIn(BaseModel):
    current_password: str = Field(min_length=1, max_length=200)
    new_password: str = Field(min_length=4, max_length=200)


class UserIn(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    role: str = auth.MEMBER
    password: str = Field(min_length=4, max_length=200)
    email: str = Field(default="", max_length=200)


class UserPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=60)
    role: str | None = None
    password: str | None = Field(default=None, min_length=4, max_length=200)
    email: str | None = Field(default=None, max_length=200)
    active: bool | None = None


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


class ChildIn(BaseModel):
    name: str = Field(min_length=1, max_length=60)


class ChildPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=60)
    active: bool | None = None


class SizeIn(BaseModel):
    category: str = Field(min_length=1, max_length=60)
    value: str = Field(min_length=1, max_length=60)
    notes: str = Field(default="", max_length=300)


class NeedIn(BaseModel):
    child_id: int
    category: str = Field(min_length=1, max_length=60)
    season: str = Field(default="", max_length=40)
    notes: str = Field(default="", max_length=300)
    add_to_shopping: bool = False


class NeedPatch(BaseModel):
    status: Literal["needed", "have"] | None = None
    notes: str | None = Field(default=None, max_length=300)


class SeasonalCheckIn(BaseModel):
    categories: list[str] = Field(min_length=1)
    season: str = Field(min_length=1, max_length=40)


class PrescriptionIn(BaseModel):
    child_id: int
    name: str = Field(min_length=1, max_length=120)
    pharmacy: str = Field(default="", max_length=120)
    contact_name: str = Field(default="", max_length=120)
    contact_phone: str = Field(default="", max_length=40)
    days_supply: int = Field(default=30, ge=1, le=365)
    refill_after_days: int = Field(default=28, ge=1, le=365)
    notes: str = Field(default="", max_length=500)
    assignee_id: int | None = None


class PrescriptionPatch(BaseModel):
    child_id: int | None = None
    name: str | None = Field(default=None, min_length=1, max_length=120)
    pharmacy: str | None = Field(default=None, max_length=120)
    contact_name: str | None = Field(default=None, max_length=120)
    contact_phone: str | None = Field(default=None, max_length=40)
    days_supply: int | None = Field(default=None, ge=1, le=365)
    refill_after_days: int | None = Field(default=None, ge=1, le=365)
    notes: str | None = Field(default=None, max_length=500)
    assignee_id: int | None = None
    active: bool | None = None


class RefillPickupIn(BaseModel):
    picked_up_on: date = Field(default_factory=date.today)
    notes: str = Field(default="", max_length=500)
    override: bool = False


class CalledIn(BaseModel):
    notes: str = Field(default="", max_length=500)


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


class DeclutterIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    room_id: int | None = None
    notes: str = Field(default="", max_length=500)


class DeclutterPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    room_id: int | None = None
    notes: str | None = Field(default=None, max_length=500)
    done: bool | None = None
    done_on: date | None = None


class ReminderSettingsIn(BaseModel):
    reminder_email: str = Field(max_length=200)
    refill_detail_in_notifications: bool | None = None


# Every /api route declares exactly one of these (enforced by tests/test_api_auth.py).
MEMBER = [Depends(auth.need_member)]  # any named user or the connector
ADMIN = [Depends(auth.need_admin)]  # admin only -> 403 for members and the connector
MANAGER = [Depends(auth.need_manager)]  # admin or connector -> 403 for members (room changes)


# ---------- Auth & users ----------


@app.get("/api/login/names")
def login_names():
    """Who can log in (first names only) so the login screen can offer a picker."""
    with get_conn() as conn:
        return [r["name"] for r in conn.execute("SELECT name FROM users WHERE active = 1 ORDER BY id")]


@app.post("/api/login")
def login(body: LoginIn):
    user = auth.login(body.name, body.password)
    if user is None:
        raise HTTPException(401, "Incorrect name or password")
    return {"token": auth.issue_token(user), "role": user["role"], "user": auth.public_user(user)}


@app.get("/api/session", dependencies=MEMBER)
def session(request: Request):
    u = request.state.user
    return {"role": request.state.role, "user": auth.public_user(u) if u is not None else None}


@app.post("/api/me/password", dependencies=MEMBER)
def change_own_password(body: PasswordChangeIn, actor: Actor = ACTOR):
    if actor.user_id is None:
        raise HTTPException(403, "The connector has no password")
    if not auth.check_user_password(actor.user_id, body.current_password):
        raise HTTPException(401, "Current password is incorrect")
    auth.set_user_password(actor.user_id, body.new_password)
    with get_conn() as conn:
        return {"token": auth.issue_token(auth.get_user(conn, actor.user_id))}


@app.get("/api/users", dependencies=MEMBER)
def list_users(include_inactive: bool = False):
    with get_conn() as conn:
        where = "" if include_inactive else "WHERE active = 1"
        return [auth.public_user(r) for r in conn.execute(f"SELECT * FROM users {where} ORDER BY id")]


def fetch_user_or_404(conn: sqlite3.Connection, user_id: int) -> sqlite3.Row:
    u = auth.get_user(conn, user_id)
    if not u:
        raise HTTPException(404, "Person not found")
    return u


def _check_role(role: str) -> str:
    if role not in auth.USER_ROLES:
        raise HTTPException(422, f"Role must be one of: {', '.join(auth.USER_ROLES)}")
    return role


@app.post("/api/admin/users", status_code=201, dependencies=ADMIN)
def admin_create_user(body: UserIn):
    with get_conn() as conn:
        if auth.find_user_by_name(conn, body.name):
            raise HTTPException(409, f"There is already someone called {body.name.strip()}")
        cur = conn.execute(
            "INSERT INTO users (name, role, password_hash, email, created_at) VALUES (?, ?, ?, ?, ?)",
            (body.name.strip(), _check_role(body.role), auth.hash_password(body.password), body.email.strip(), now_iso()),
        )
        return auth.public_user(fetch_user_or_404(conn, cur.lastrowid))


@app.patch("/api/admin/users/{user_id}", dependencies=ADMIN)
def admin_update_user(user_id: int, body: UserPatch, actor: Actor = ACTOR):
    with get_conn() as conn:
        fetch_user_or_404(conn, user_id)
        fields = body.model_dump(exclude_unset=True)
        password = fields.pop("password", None)
        if "name" in fields:
            fields["name"] = fields["name"].strip()
            dup = auth.find_user_by_name(conn, fields["name"])
            if dup and dup["id"] != user_id:
                raise HTTPException(409, f"There is already someone called {fields['name']}")
        if "role" in fields:
            _check_role(fields["role"])
        if "active" in fields:
            fields["active"] = int(fields["active"])
        if user_id == actor.user_id and (fields.get("role") == auth.MEMBER or fields.get("active") == 0):
            raise HTTPException(400, "You can't demote or deactivate yourself")
        if fields:
            sets = ", ".join(f"{k} = ?" for k in fields)
            conn.execute(f"UPDATE users SET {sets} WHERE id = ?", (*fields.values(), user_id))
    if password:
        auth.set_user_password(user_id, password)
    with get_conn() as conn:
        return auth.public_user(fetch_user_or_404(conn, user_id))


@app.delete("/api/admin/users/{user_id}", status_code=204, dependencies=ADMIN)
def admin_delete_user(user_id: int, actor: Actor = ACTOR):
    if user_id == actor.user_id:
        raise HTTPException(400, "You can't delete yourself")
    with get_conn() as conn:
        fetch_user_or_404(conn, user_id)
        conn.execute("DELETE FROM users WHERE id = ?", (user_id,))


# ---------- MCP connector (Claude) ----------
# The connector role only ever holds OAuth tokens minted by oauth.py; revoking them here
# does not touch anyone's sessions.


@app.get("/api/admin/connector", dependencies=ADMIN)
def connector_status():
    return oauth.connector_status()


@app.delete("/api/admin/connector", dependencies=ADMIN)
def revoke_connector():
    return {"revoked_tokens": oauth.revoke_all()}


def _approve_page(req: dict, pending_id: str, error: str | None = None) -> HTMLResponse:
    err = f'<p class="err">{escape(error)}</p>' if error else ""
    return HTMLResponse(
        f"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Connect to Home</title>
<style>body{{font-family:system-ui,sans-serif;background:#f5f5f4;margin:0;display:flex;justify-content:center;padding:2rem 1rem}}
main{{background:#fff;border-radius:1rem;padding:1.5rem;max-width:26rem;width:100%;box-shadow:0 1px 3px rgba(0,0,0,.1)}}
h1{{font-size:1.25rem;margin:0 0 .5rem}}p{{color:#57534e;font-size:.95rem}}ul{{color:#57534e;font-size:.9rem;padding-left:1.2rem}}
input{{width:100%;box-sizing:border-box;padding:.6rem .75rem;border:1px solid #d6d3d1;border-radius:.5rem;font-size:1rem;margin:.25rem 0 1rem}}
button{{padding:.6rem 1rem;border-radius:.5rem;border:0;font-size:1rem;cursor:pointer}}
.ok{{background:#0f766e;color:#fff}}.no{{background:#e7e5e4;color:#292524;margin-left:.5rem}}.err{{color:#b91c1c}}</style></head>
<body><main><h1>Let <b>{escape(req["client_name"])}</b> use Home?</h1>
<p>It will be able to read and change rooms, tasks, upkeep, pantry and the shopping list as the <b>connector</b> role.
It will <b>not</b> see people's passwords or admin settings. You can revoke it any time from Manage.</p>
{err}<form method="post"><input type="hidden" name="req" value="{escape(pending_id)}">
<label>Admin password<input type="password" name="password" autofocus required></label>
<button class="ok" name="decision" value="approve">Allow</button><button class="no" name="decision" value="deny">Cancel</button></form>
</main></body></html>""",
        status_code=401 if error else 200,
    )


@app.get(oauth.APPROVE_PATH, include_in_schema=False)
def connector_approve_page(req: str):
    pending = oauth.pending_request(req)
    if not pending:
        return HTMLResponse("<p>This connection request has expired. Start again from Claude.</p>", status_code=410)
    return _approve_page(pending, req)


@app.post(oauth.APPROVE_PATH, include_in_schema=False)
def connector_approve(req: str = Form(), password: str = Form(default=""), decision: str = Form(default="approve")):
    pending = oauth.pending_request(req)
    if not pending:
        return HTMLResponse("<p>This connection request has expired. Start again from Claude.</p>", status_code=410)
    if decision != "approve":
        return RedirectResponse(oauth.deny(req) or "/", status_code=303)
    if not auth.admin_for_password(password):
        return _approve_page(pending, req, "Incorrect admin password")
    return RedirectResponse(oauth.approve(req) or "/", status_code=303)


# ---------- Helpers ----------


def task_status(last_completed_at: str | None, frequency_days: int | None, due_on: str | None = None) -> str:
    if frequency_days is None:
        # a one-off errand: due on its date (if it has one) until it's done
        if due_on and last_completed_at is None:
            return "overdue" if date.fromisoformat(due_on) < date.today() else "due"
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


def user_name(conn: sqlite3.Connection, user_id: int | None) -> str | None:
    if user_id is None:
        return None
    row = conn.execute("SELECT name FROM users WHERE id = ?", (user_id,)).fetchone()
    return row["name"] if row else None


def serialize_task(conn: sqlite3.Connection, t: sqlite3.Row) -> dict:
    last = conn.execute(
        "SELECT completed_by, completed_at, user_id FROM completions WHERE task_id = ? ORDER BY completed_at DESC LIMIT 1",
        (t["id"],),
    ).fetchone()
    notes = conn.execute(
        "SELECT * FROM notes WHERE task_id = ? AND resolved = 0 ORDER BY created_at DESC",
        (t["id"],),
    ).fetchall()
    room = conn.execute("SELECT name, slug FROM rooms WHERE id = ?", (t["room_id"],)).fetchone() if t["room_id"] else None
    last_at = last["completed_at"] if last else None
    return {
        "id": t["id"],
        "room_id": t["room_id"],
        "room_name": room["name"] if room else None,
        "room_slug": room["slug"] if room else None,
        "title": t["title"],
        "description": t["description"],
        "frequency_days": t["frequency_days"],
        "sort_order": t["sort_order"],
        "active": bool(t["active"]),
        "assignee_id": t["assignee_id"],
        "assignee": user_name(conn, t["assignee_id"]),
        "due_on": t["due_on"],
        "created_at": t["created_at"],
        "last_completed_at": last_at,
        "last_completed_by": last["completed_by"] if last else None,
        "last_completed_by_user_id": last["user_id"] if last else None,
        "status": task_status(last_at, t["frequency_days"], t["due_on"]),
        # a room-less one-off is finished once it has been completed
        "done": t["room_id"] is None and t["frequency_days"] is None and last_at is not None,
        "notes": [serialize_note(n) for n in notes],
    }


def serialize_shopping_item(conn: sqlite3.Connection, s: sqlite3.Row) -> dict:
    return {
        "id": s["id"],
        "name": s["name"],
        "notes": s["notes"],
        "assignee_id": s["assignee_id"],
        "assignee": user_name(conn, s["assignee_id"]),
        "due_on": s["due_on"],
        "added_by": s["added_by"],
        "created_at": s["created_at"],
        "bought_at": s["bought_at"],
        "bought_by": s["bought_by"],
        "done": s["bought_at"] is not None,
        "overdue": s["bought_at"] is None and s["due_on"] is not None and date.fromisoformat(s["due_on"]) < date.today(),
    }


def check_assignment(conn: sqlite3.Connection, actor: Actor, assignee_id: int | None, current: int | None = None) -> None:
    """Admins and the connector can assign to anyone; a member may only put things on their own list."""
    if assignee_id == current:
        return
    if assignee_id is not None:
        fetch_user_or_404(conn, assignee_id)
    if actor.can_assign:
        return
    if current is None and assignee_id == actor.user_id:
        return
    raise HTTPException(403, "Only an admin (or Claude) can assign things to other people")


def notify_assignment(conn: sqlite3.Connection, actor: Actor, assignee_id: int | None, kind: str, ref_id: int, what: str) -> None:
    """One notification per assignment. Nothing when unassigned or assigned to yourself."""
    if assignee_id is None or assignee_id == actor.user_id:
        return
    by = actor.attribution("Someone")
    noun = "a task" if kind == "task" else "something to buy"
    conn.execute(
        "INSERT INTO notifications (user_id, kind, ref_id, message, created_by, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (assignee_id, kind, ref_id, f"{by} put {noun} on your list: {what}", by, now_iso()),
    )


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
        "active": bool(r["active"]),
        "task_count": len(serialized),
        "due_count": sum(1 for t in serialized if t["status"] != "ok"),
        "note_count": sum(len(t["notes"]) for t in serialized),
    }
    if with_tasks:
        out["tasks"] = serialized
    return out


# Active tasks that aren't in an archived room (room-less errands included).
ACTIVE_TASKS_SQL = (
    "SELECT t.* FROM tasks t LEFT JOIN rooms r ON r.id = t.room_id "
    "WHERE t.active = 1 AND (t.room_id IS NULL OR r.active = 1) ORDER BY t.sort_order, t.id"
)


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
# Changing rooms is admin/connector only (MANAGER); reading is open to everyone logged in.


def _norm_room_name(name: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", "", name.lower().replace("'", "")).replace(" room", "").strip()


def find_similar_room(conn: sqlite3.Connection, name: str) -> sqlite3.Row | None:
    """An existing room whose name is the same or nearly so ("dining room" vs "Dining Room",
    "Kitchen" vs "Kitchn"). Only active rooms count."""
    target = _norm_room_name(name)
    if not target:
        return None
    best, best_score = None, 0.0
    for r in conn.execute("SELECT * FROM rooms WHERE active = 1"):
        existing = _norm_room_name(r["name"])
        if existing == target or slugify(r["name"]) == slugify(name):
            return r
        score = SequenceMatcher(None, existing, target).ratio()
        if score > best_score:
            best, best_score = r, score
    return best if best_score >= 0.88 else None


def resolve_room_slug(conn: sqlite3.Connection, slug: str) -> sqlite3.Row | None:
    """Current slug first, then old slugs left behind by renames (NFC tags on the wall)."""
    r = conn.execute("SELECT * FROM rooms WHERE slug = ?", (slug,)).fetchone()
    if r:
        return r
    return conn.execute(
        "SELECT r.* FROM room_slug_aliases a JOIN rooms r ON r.id = a.room_id WHERE a.slug = ?", (slug,)
    ).fetchone()


def _unique_slug(conn: sqlite3.Connection, base: str, room_id: int | None = None) -> str:
    slug, n = base, 2
    while True:
        taken = conn.execute(
            "SELECT id FROM rooms WHERE slug = ? UNION SELECT room_id FROM room_slug_aliases WHERE slug = ?", (slug, slug)
        ).fetchall()
        if not taken or all(t[0] == room_id for t in taken):
            return slug
        slug, n = f"{base}-{n}", n + 1


@app.get("/api/rooms", dependencies=MEMBER)
def list_rooms(include_archived: bool = False):
    with get_conn() as conn:
        where = "" if include_archived else "WHERE active = 1"
        rows = conn.execute(f"SELECT * FROM rooms {where} ORDER BY sort_order, id").fetchall()
        return [serialize_room(conn, r, with_tasks=False) for r in rows]


@app.post("/api/rooms", status_code=201, dependencies=MANAGER)
def create_room(body: RoomIn, response: Response):
    """Creates a room. If one with (nearly) the same name exists, returns that one flagged as
    a duplicate (200) instead of creating a second."""
    name = body.name.strip()
    with get_conn() as conn:
        existing = find_similar_room(conn, name)
        if existing:
            response.status_code = 200
            return {**serialize_room(conn, existing, with_tasks=True), "duplicate": True, "duplicate_of": existing["name"]}
        slug = _unique_slug(conn, slugify(body.slug or name))
        max_order = conn.execute("SELECT COALESCE(MAX(sort_order), -1) FROM rooms").fetchone()[0]
        cur = conn.execute(
            "INSERT INTO rooms (slug, name, icon, sort_order) VALUES (?, ?, ?, ?)",
            (slug, name, body.icon, body.sort_order if body.sort_order is not None else max_order + 1),
        )
        return {**serialize_room(conn, fetch_room_or_404(conn, cur.lastrowid), with_tasks=True), "duplicate": False}


@app.get("/api/rooms/{slug}", dependencies=MEMBER)
def get_room(slug: str):
    """By current slug or an old one (tags written before a rename). Archived rooms still load
    so their history stays reachable."""
    with get_conn() as conn:
        r = resolve_room_slug(conn, slug)
        if not r:
            raise HTTPException(404, "Room not found")
        return serialize_room(conn, r, with_tasks=True)


@app.patch("/api/rooms/{room_id}", dependencies=MANAGER)
def update_room(room_id: int, body: RoomPatch):
    """Renaming regenerates the slug; the old slug is kept as an alias so existing NFC tags
    still open the room. Same id, same task history."""
    with get_conn() as conn:
        room = fetch_room_or_404(conn, room_id)
        fields = body.model_dump(exclude_none=True)
        if "name" in fields:
            fields["name"] = fields["name"].strip()
            if fields["name"] != room["name"]:
                dup = find_similar_room(conn, fields["name"])
                if dup and dup["id"] != room_id:
                    raise HTTPException(409, f"There is already a room called {dup['name']}")
                fields.setdefault("slug", fields["name"])
        if "slug" in fields:
            new_slug = _unique_slug(conn, slugify(fields["slug"]), room_id)
            if new_slug != room["slug"]:
                conn.execute(
                    "INSERT OR REPLACE INTO room_slug_aliases (slug, room_id) VALUES (?, ?)", (room["slug"], room_id)
                )
                conn.execute("DELETE FROM room_slug_aliases WHERE slug = ?", (new_slug,))
            fields["slug"] = new_slug
        if "active" in fields:
            fields["active"] = int(fields["active"])
        if fields:
            sets = ", ".join(f"{k} = ?" for k in fields)
            conn.execute(f"UPDATE rooms SET {sets} WHERE id = ?", (*fields.values(), room_id))
        return serialize_room(conn, fetch_room_or_404(conn, room_id), with_tasks=True)


@app.delete("/api/rooms/{room_id}", status_code=204, dependencies=MANAGER)
def delete_room(room_id: int):
    """Refused while the room still has tasks (deleting would erase their history); archive instead."""
    with get_conn() as conn:
        room = fetch_room_or_404(conn, room_id)
        n = conn.execute("SELECT COUNT(*) FROM tasks WHERE room_id = ?", (room_id,)).fetchone()[0]
        if n:
            raise HTTPException(
                409,
                f"{room['name']} still has {n} task{'s' if n != 1 else ''} (and their history). "
                "Deactivate the room instead of deleting it, or move/delete its tasks first.",
            )
        conn.execute("DELETE FROM rooms WHERE id = ?", (room_id,))


# ---------- Tasks ----------


@app.post("/api/tasks", status_code=201, dependencies=MEMBER)
def create_task(body: TaskIn, actor: Actor = ACTOR):
    with get_conn() as conn:
        if body.room_id is not None:
            fetch_room_or_404(conn, body.room_id)
        check_assignment(conn, actor, body.assignee_id)
        max_order = conn.execute(
            "SELECT COALESCE(MAX(sort_order), -1) FROM tasks WHERE room_id IS ?", (body.room_id,)
        ).fetchone()[0]
        cur = conn.execute(
            "INSERT INTO tasks (room_id, title, description, frequency_days, sort_order, assignee_id, due_on, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                body.room_id,
                body.title.strip(),
                body.description.strip(),
                body.frequency_days,
                max_order + 1,
                body.assignee_id,
                body.due_on.isoformat() if body.due_on else None,
                now_iso(),
            ),
        )
        notify_assignment(conn, actor, body.assignee_id, "task", cur.lastrowid, body.title.strip())
        return serialize_task(conn, fetch_task_or_404(conn, cur.lastrowid))


@app.patch("/api/tasks/{task_id}", dependencies=MEMBER)
def update_task(task_id: int, body: TaskPatch, actor: Actor = ACTOR):
    with get_conn() as conn:
        t = fetch_task_or_404(conn, task_id)
        fields = body.model_dump(exclude_unset=True)
        if "frequency_days" in fields and not fields["frequency_days"]:
            fields["frequency_days"] = None
        if "active" in fields:
            fields["active"] = int(fields["active"])
        if "title" in fields and fields["title"]:
            fields["title"] = fields["title"].strip()
        if "due_on" in fields and fields["due_on"] is not None:
            fields["due_on"] = fields["due_on"].isoformat()
        reassigned = "assignee_id" in fields and fields["assignee_id"] != t["assignee_id"]
        if reassigned:
            check_assignment(conn, actor, fields["assignee_id"], t["assignee_id"])
        if fields:
            sets = ", ".join(f"{k} = ?" for k in fields)
            conn.execute(f"UPDATE tasks SET {sets} WHERE id = ?", (*fields.values(), task_id))
        if reassigned:
            notify_assignment(conn, actor, fields["assignee_id"], "task", task_id, fields.get("title") or t["title"])
        return serialize_task(conn, fetch_task_or_404(conn, task_id))


@app.delete("/api/tasks/{task_id}", status_code=204, dependencies=MEMBER)
def delete_task(task_id: int):
    with get_conn() as conn:
        fetch_task_or_404(conn, task_id)
        conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))


@app.post("/api/tasks/{task_id}/complete", status_code=201, dependencies=MEMBER)
def complete_task(task_id: int, body: CompleteIn, actor: Actor = ACTOR):
    """A logged-in user's completion is always recorded under their own name."""
    with get_conn() as conn:
        fetch_task_or_404(conn, task_id)
        by = actor.name if actor.user_id else (body.completed_by.strip() or actor.name)
        conn.execute(
            "INSERT INTO completions (task_id, completed_by, completed_at, user_id) VALUES (?, ?, ?, ?)",
            (task_id, by, now_iso(), actor.user_id),
        )
        return serialize_task(conn, fetch_task_or_404(conn, task_id))


@app.delete("/api/completions/{completion_id}", status_code=204, dependencies=MEMBER)
def undo_completion(completion_id: int):
    with get_conn() as conn:
        c = conn.execute("SELECT * FROM completions WHERE id = ?", (completion_id,)).fetchone()
        if not c:
            raise HTTPException(404, "Completion not found")
        conn.execute("DELETE FROM completions WHERE id = ?", (completion_id,))


@app.get("/api/tasks/{task_id}/history", dependencies=MEMBER)
def task_history(task_id: int, limit: int = 50):
    with get_conn() as conn:
        fetch_task_or_404(conn, task_id)
        rows = conn.execute(
            "SELECT id, completed_by, completed_at, user_id FROM completions WHERE task_id = ? ORDER BY completed_at DESC LIMIT ?",
            (task_id, limit),
        ).fetchall()
        return [dict(r) for r in rows]


# ---------- Notes ----------


@app.post("/api/tasks/{task_id}/notes", status_code=201, dependencies=MEMBER)
def add_note(task_id: int, body: NoteIn):
    with get_conn() as conn:
        fetch_task_or_404(conn, task_id)
        cur = conn.execute(
            "INSERT INTO notes (task_id, author, body, needs_purchase, created_at) VALUES (?, ?, ?, ?, ?)",
            (task_id, body.author.strip(), body.body.strip(), int(body.needs_purchase), now_iso()),
        )
        n = conn.execute("SELECT * FROM notes WHERE id = ?", (cur.lastrowid,)).fetchone()
        return serialize_note(n)


@app.patch("/api/notes/{note_id}", dependencies=MEMBER)
def update_note(note_id: int, body: NotePatch):
    with get_conn() as conn:
        n = conn.execute("SELECT * FROM notes WHERE id = ?", (note_id,)).fetchone()
        if not n:
            raise HTTPException(404, "Note not found")
        conn.execute("UPDATE notes SET resolved = ? WHERE id = ?", (int(body.resolved), note_id))
        return serialize_note(conn.execute("SELECT * FROM notes WHERE id = ?", (note_id,)).fetchone())


@app.delete("/api/notes/{note_id}", status_code=204, dependencies=MEMBER)
def delete_note(note_id: int):
    with get_conn() as conn:
        if not conn.execute("SELECT 1 FROM notes WHERE id = ?", (note_id,)).fetchone():
            raise HTTPException(404, "Note not found")
        conn.execute("DELETE FROM notes WHERE id = ?", (note_id,))


@app.get("/api/shopping", dependencies=MEMBER)
def shopping_list():
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT n.*, t.title AS task_title, r.name AS room_name, r.slug AS room_slug
            FROM notes n
            JOIN tasks t ON t.id = n.task_id
            LEFT JOIN rooms r ON r.id = t.room_id
            WHERE n.needs_purchase = 1 AND n.resolved = 0
            ORDER BY n.created_at DESC
            """
        ).fetchall()
        return [
            {**serialize_note(r), "task_title": r["task_title"], "room_name": r["room_name"], "room_slug": r["room_slug"]}
            for r in rows
        ]


@app.get("/api/activity", dependencies=MANAGER)
def recent_activity(limit: int = 30):
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT c.id, c.completed_by, c.completed_at, c.user_id, t.title AS task_title, r.name AS room_name, r.slug AS room_slug
            FROM completions c
            JOIN tasks t ON t.id = c.task_id
            LEFT JOIN rooms r ON r.id = t.room_id
            ORDER BY c.completed_at DESC LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]


# ---------- Shopping items (standalone, assignable) ----------


def fetch_shopping_or_404(conn: sqlite3.Connection, item_id: int) -> sqlite3.Row:
    s = conn.execute("SELECT * FROM shopping_items WHERE id = ?", (item_id,)).fetchone()
    if not s:
        raise HTTPException(404, "Shopping item not found")
    return s


@app.get("/api/shopping-items", dependencies=MEMBER)
def list_shopping_items(include_done: bool = False):
    with get_conn() as conn:
        where = "" if include_done else "WHERE bought_at IS NULL"
        rows = conn.execute(f"SELECT * FROM shopping_items {where} ORDER BY created_at, id").fetchall()
        return [serialize_shopping_item(conn, s) for s in rows]


@app.post("/api/shopping-items", status_code=201, dependencies=MEMBER)
def create_shopping_item(body: ShoppingItemIn, actor: Actor = ACTOR):
    with get_conn() as conn:
        check_assignment(conn, actor, body.assignee_id)
        cur = conn.execute(
            "INSERT INTO shopping_items (name, notes, assignee_id, due_on, added_by, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (body.name.strip(), body.notes.strip(), body.assignee_id, body.due_on.isoformat() if body.due_on else None, actor.name, now_iso()),
        )
        notify_assignment(conn, actor, body.assignee_id, "shopping", cur.lastrowid, body.name.strip())
        return serialize_shopping_item(conn, fetch_shopping_or_404(conn, cur.lastrowid))


@app.patch("/api/shopping-items/{item_id}", dependencies=MEMBER)
def update_shopping_item(item_id: int, body: ShoppingItemPatch, actor: Actor = ACTOR):
    with get_conn() as conn:
        s = fetch_shopping_or_404(conn, item_id)
        fields = body.model_dump(exclude_unset=True)
        bought = fields.pop("bought", None)
        if bought is not None:
            fields["bought_at"] = now_iso() if bought else None
            fields["bought_by"] = actor.name if bought else ""
        for k in ("name", "notes"):
            if fields.get(k) is not None:
                fields[k] = fields[k].strip()
        if fields.get("due_on") is not None:
            fields["due_on"] = fields["due_on"].isoformat()
        reassigned = "assignee_id" in fields and fields["assignee_id"] != s["assignee_id"]
        if reassigned:
            check_assignment(conn, actor, fields["assignee_id"], s["assignee_id"])
        if fields:
            sets = ", ".join(f"{k} = ?" for k in fields)
            conn.execute(f"UPDATE shopping_items SET {sets} WHERE id = ?", (*fields.values(), item_id))
        if reassigned:
            notify_assignment(conn, actor, fields["assignee_id"], "shopping", item_id, fields.get("name") or s["name"])
        return serialize_shopping_item(conn, fetch_shopping_or_404(conn, item_id))


@app.delete("/api/shopping-items/{item_id}", status_code=204, dependencies=MEMBER)
def delete_shopping_item(item_id: int):
    with get_conn() as conn:
        fetch_shopping_or_404(conn, item_id)
        conn.execute("DELETE FROM shopping_items WHERE id = ?", (item_id,))


# ---------- Per-person lists & notifications ----------


def person_list(conn: sqlite3.Connection, user: sqlite3.Row, include_done: bool = False) -> dict:
    """Everything assigned to one person - tasks and shopping together - oldest first."""
    items = []
    for row in conn.execute("SELECT * FROM tasks WHERE assignee_id = ? AND active = 1", (user["id"],)):
        t = serialize_task(conn, row)
        if t["done"] and not include_done:
            continue
        items.append(
            {
                "kind": "task",
                "id": t["id"],
                "title": t["title"],
                "notes": t["description"],
                "room_name": t["room_name"],
                "room_slug": t["room_slug"],
                "due_on": t["due_on"],
                "frequency_days": t["frequency_days"],
                "status": t["status"],
                "overdue": t["status"] == "overdue",
                "done": t["done"],
                "created_at": t["created_at"],
                "last_completed_at": t["last_completed_at"],
                "last_completed_by": t["last_completed_by"],
            }
        )
    for row in conn.execute("SELECT * FROM shopping_items WHERE assignee_id = ?", (user["id"],)):
        s = serialize_shopping_item(conn, row)
        if s["done"] and not include_done:
            continue
        items.append(
            {
                "kind": "shopping",
                "id": s["id"],
                "title": s["name"],
                "notes": s["notes"],
                "room_name": None,
                "room_slug": None,
                "due_on": s["due_on"],
                "frequency_days": None,
                "status": "overdue" if s["overdue"] else "ok",
                "overdue": s["overdue"],
                "done": s["done"],
                "created_at": s["created_at"],
                "last_completed_at": s["bought_at"],
                "last_completed_by": s["bought_by"] or None,
            }
        )
    items.sort(key=lambda i: (i["created_at"], i["kind"], i["id"]))
    return {
        "user": auth.public_user(user),
        "items": items,
        "overdue_count": sum(1 for i in items if i["overdue"] and not i["done"]),
    }


@app.get("/api/users/{user_id}/list", dependencies=MEMBER)
def user_list(user_id: int, include_done: bool = False):
    with get_conn() as conn:
        return person_list(conn, fetch_user_or_404(conn, user_id), include_done)


@app.get("/api/me/list", dependencies=MEMBER)
def my_list(include_done: bool = False, actor: Actor = ACTOR):
    if actor.user_id is None:
        raise HTTPException(403, "The connector has no list of its own; use /api/users/{id}/list")
    with get_conn() as conn:
        return person_list(conn, fetch_user_or_404(conn, actor.user_id), include_done)


@app.get("/api/me/notifications", dependencies=MEMBER)
def my_notifications(unread_only: bool = False, limit: int = 50, actor: Actor = ACTOR):
    if actor.user_id is None:
        return []
    with get_conn() as conn:
        where = "AND read_at IS NULL" if unread_only else ""
        rows = conn.execute(
            f"SELECT * FROM notifications WHERE user_id = ? {where} ORDER BY created_at DESC, id DESC LIMIT ?",
            (actor.user_id, limit),
        ).fetchall()
        return [dict(r) for r in rows]


@app.post("/api/me/notifications/read", dependencies=MEMBER)
def mark_notifications_read(actor: Actor = ACTOR):
    if actor.user_id is None:
        return {"marked": 0}
    with get_conn() as conn:
        cur = conn.execute("UPDATE notifications SET read_at = ? WHERE user_id = ? AND read_at IS NULL", (now_iso(), actor.user_id))
        return {"marked": cur.rowcount}


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


@app.get("/api/pantry", dependencies=MEMBER)
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


@app.post("/api/pantry", status_code=201, dependencies=MEMBER)
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


@app.patch("/api/pantry/{item_id}", dependencies=MEMBER)
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


@app.delete("/api/pantry/{item_id}", status_code=204, dependencies=MEMBER)
def delete_pantry_item(item_id: int):
    with get_conn() as conn:
        if not conn.execute("SELECT 1 FROM pantry_items WHERE id = ?", (item_id,)).fetchone():
            raise HTTPException(404, "Item not found")
        conn.execute("DELETE FROM pantry_items WHERE id = ?", (item_id,))


# ---------- Prescription refills (children, prescriptions, pickups) ----------
# Reads, pickups and "called" are for every named user; adding/editing a prescription is admin (or Claude).


def fetch_child_or_404(conn: sqlite3.Connection, child_id: int) -> sqlite3.Row:
    c = conn.execute("SELECT * FROM children WHERE id = ?", (child_id,)).fetchone()
    if not c:
        raise HTTPException(404, "Child not found")
    return c


def fetch_prescription_or_404(conn: sqlite3.Connection, rx_id: int) -> sqlite3.Row:
    p = conn.execute("SELECT * FROM prescriptions WHERE id = ?", (rx_id,)).fetchone()
    if not p:
        raise HTTPException(404, "Prescription not found")
    return p


def serialize_child(c: sqlite3.Row) -> dict:
    return {"id": c["id"], "name": c["name"], "active": bool(c["active"])}


@app.get("/api/children", dependencies=MEMBER)
def list_children(include_inactive: bool = False):
    with get_conn() as conn:
        where = "" if include_inactive else "WHERE active = 1"
        return [serialize_child(c) for c in conn.execute(f"SELECT * FROM children {where} ORDER BY name")]


@app.post("/api/children", status_code=201, dependencies=MANAGER)
def create_child(body: ChildIn, response: Response):
    """Adds a child. A name closely matching an existing child returns that child flagged as a
    duplicate (200) instead of adding a second."""
    name = body.name.strip()
    with get_conn() as conn:
        kids = [dict(c) for c in conn.execute("SELECT * FROM children")]
        dup = _closest(name, kids)
        if dup:
            response.status_code = 200
            return {**serialize_child(dup), "duplicate": True, "duplicate_of": dup["name"]}
        cur = conn.execute("INSERT INTO children (name) VALUES (?)", (name,))
        return {**serialize_child(fetch_child_or_404(conn, cur.lastrowid)), "duplicate": False}


def _closest(name: str, rows: list[dict], threshold: float = 0.9) -> dict | None:
    best = max(rows, key=lambda r: similarity(name, r["name"]), default=None)
    return best if best and similarity(name, best["name"]) >= threshold else None


@app.patch("/api/children/{child_id}", dependencies=MANAGER)
def update_child(child_id: int, body: ChildPatch):
    with get_conn() as conn:
        fetch_child_or_404(conn, child_id)
        fields = body.model_dump(exclude_unset=True)
        if "name" in fields:
            fields["name"] = fields["name"].strip()
        if "active" in fields:
            fields["active"] = int(fields["active"])
        if fields:
            sets = ", ".join(f"{k} = ?" for k in fields)
            conn.execute(f"UPDATE children SET {sets} WHERE id = ?", (*fields.values(), child_id))
        return serialize_child(fetch_child_or_404(conn, child_id))


@app.delete("/api/children/{child_id}", status_code=204, dependencies=MANAGER)
def delete_child(child_id: int):
    """Refused while the child still has prescriptions (their pickup history would go with them);
    deactivate instead, or delete the prescriptions first."""
    with get_conn() as conn:
        c = fetch_child_or_404(conn, child_id)
        n = conn.execute("SELECT COUNT(*) FROM prescriptions WHERE child_id = ?", (child_id,)).fetchone()[0]
        if n:
            raise HTTPException(
                409,
                f"{c['name']} still has {n} prescription{'s' if n != 1 else ''}. Delete those first, "
                "or mark the child inactive to keep the history.",
            )
        conn.execute("DELETE FROM children WHERE id = ?", (child_id,))


# ---------- Children's sizes and needs (every named user; Claude too) ----------


@app.get("/api/children/{child_id}", dependencies=MEMBER)
def get_child(child_id: int):
    """A child with their current sizes (each with age and a stale flag) and open needs."""
    with get_conn() as conn:
        c = fetch_child_or_404(conn, child_id)
        return {
            **serialize_child(c),
            "sizes": kids.sizes(conn, child_id),
            "needs": kids.needs(conn, child_id),
            "prescription_count": conn.execute(
                "SELECT COUNT(*) FROM prescriptions WHERE child_id = ? AND active = 1", (child_id,)
            ).fetchone()[0],
        }


@app.get("/api/sizes", dependencies=MEMBER)
def list_sizes(category: str | None = None):
    with get_conn() as conn:
        return kids.sizes(conn, category=kids.canonical_category(conn, category) if category else None)


@app.put("/api/children/{child_id}/sizes", dependencies=MEMBER)
def set_child_size(child_id: int, body: SizeIn, actor: Actor = ACTOR):
    """Sets (or replaces) the child's current size in one category, recording who updated it."""
    with get_conn() as conn:
        fetch_child_or_404(conn, child_id)
        cat = kids.canonical_category(conn, body.category)
        return kids.set_size(conn, child_id, cat, body.value, body.notes, actor.attribution("Someone"), actor.user_id)


@app.get("/api/needs", dependencies=MEMBER)
def list_needs(child_id: int | None = None, category: str | None = None, season: str | None = None, status: str = "needed"):
    if status not in (*kids.NEED_STATUSES, "all"):
        raise HTTPException(400, f"status should be needed, have or all, not '{status}'")
    with get_conn() as conn:
        cat = kids.canonical_category(conn, category) if category else None
        return kids.needs(conn, child_id, cat, season, status)


@app.post("/api/needs", status_code=201, dependencies=MEMBER)
def create_need(body: NeedIn, response: Response, actor: Actor = ACTOR):
    """Records that a child needs something. An open need for the same child, category and season
    is returned flagged as a duplicate (200) instead of being added twice."""
    with get_conn() as conn:
        child = fetch_child_or_404(conn, body.child_id)
        cat = kids.canonical_category(conn, body.category)
        season = body.season.strip()
        dup = kids.open_need(conn, body.child_id, cat, season)
        if dup:
            response.status_code = 200
            return {**dup, "duplicate": True}
        shopping_id = None
        if body.add_to_shopping:
            label = f"{cat} for {child['name']}" + (f" ({season})" if season else "")
            shopping_id = conn.execute(
                "INSERT INTO shopping_items (name, notes, added_by, created_at) VALUES (?, ?, ?, ?)",
                (label, body.notes.strip(), actor.attribution("Someone"), now_iso()),
            ).lastrowid
        cur = conn.execute(
            "INSERT INTO child_needs (child_id, category, season, notes, shopping_item_id, created_at, created_by) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (body.child_id, cat, season, body.notes.strip(), shopping_id, now_iso(), actor.attribution("Someone")),
        )
        return {**kids.fetch_need(conn, cur.lastrowid), "duplicate": False}


@app.patch("/api/needs/{need_id}", dependencies=MEMBER)
def update_need(need_id: int, body: NeedPatch, actor: Actor = ACTOR):
    """status 'have' marks it handled (recording who); 'needed' reopens it."""
    with get_conn() as conn:
        n = kids.fetch_need(conn, need_id)
        if not n:
            raise HTTPException(404, "Need not found")
        if body.notes is not None:
            conn.execute("UPDATE child_needs SET notes = ? WHERE id = ?", (body.notes.strip(), need_id))
        if body.status == "have":
            conn.execute(
                "UPDATE child_needs SET status = 'have', resolved_at = ?, resolved_by = ? WHERE id = ?",
                (now_iso(), actor.attribution("Someone"), need_id),
            )
            if n["shopping_item_id"]:
                conn.execute(
                    "UPDATE shopping_items SET bought_at = ?, bought_by = ? WHERE id = ? AND bought_at IS NULL",
                    (now_iso(), actor.attribution("Someone"), n["shopping_item_id"]),
                )
        elif body.status == "needed":
            conn.execute("UPDATE child_needs SET status = 'needed', resolved_at = NULL, resolved_by = '' WHERE id = ?", (need_id,))
        return kids.fetch_need(conn, need_id)


@app.delete("/api/needs/{need_id}", status_code=204, dependencies=MEMBER)
def delete_need(need_id: int):
    with get_conn() as conn:
        if not kids.fetch_need(conn, need_id):
            raise HTTPException(404, "Need not found")
        conn.execute("DELETE FROM child_needs WHERE id = ?", (need_id,))


@app.post("/api/needs/seasonal-check", dependencies=MEMBER)
def seasonal_check(body: SeasonalCheckIn):
    """Every active child crossed with every category: current size (and its age) plus whether a
    need for that season is open, handled, or never recorded."""
    with get_conn() as conn:
        cats = [kids.canonical_category(conn, c) for c in body.categories if c.strip()]
        return kids.seasonal_check(conn, cats, body.season.strip())


@app.get("/api/prescriptions", dependencies=MEMBER)
def list_prescriptions(child_id: int | None = None, include_inactive: bool = False):
    with get_conn() as conn:
        where, params = [], []
        if not include_inactive:
            where.append("p.active = 1 AND c.active = 1")
        if child_id is not None:
            where.append("p.child_id = ?")
            params.append(child_id)
        sql = "SELECT p.* FROM prescriptions p JOIN children c ON c.id = p.child_id"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY c.name, p.name"
        return [refills.serialize_prescription(conn, p) for p in conn.execute(sql, params)]


@app.get("/api/refills", dependencies=MEMBER)
def list_due_refills():
    """Prescriptions that are refill_due or urgent. An item stays here until a pickup is logged."""
    with get_conn() as conn:
        return refills.due_refills(conn)


@app.post("/api/prescriptions", status_code=201, dependencies=MANAGER)
def create_prescription(body: PrescriptionIn, response: Response):
    """A prescription whose name closely matches one the same child already has is returned
    flagged as a duplicate (200) instead of being added twice."""
    with get_conn() as conn:
        fetch_child_or_404(conn, body.child_id)
        if body.assignee_id is not None:
            fetch_user_or_404(conn, body.assignee_id)
        existing = [
            dict(p) for p in conn.execute("SELECT * FROM prescriptions WHERE child_id = ? AND active = 1", (body.child_id,))
        ]
        dup = _closest(body.name.strip(), existing)
        if dup:
            response.status_code = 200
            return {**refills.serialize_prescription(conn, fetch_prescription_or_404(conn, dup["id"])), "duplicate": True, "duplicate_of": dup["name"]}
        cur = conn.execute(
            "INSERT INTO prescriptions (child_id, name, pharmacy, contact_name, contact_phone, days_supply, "
            "refill_after_days, notes, assignee_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                body.child_id,
                body.name.strip(),
                body.pharmacy.strip(),
                body.contact_name.strip(),
                body.contact_phone.strip(),
                body.days_supply,
                body.refill_after_days,
                body.notes.strip(),
                body.assignee_id,
            ),
        )
        return {**refills.serialize_prescription(conn, fetch_prescription_or_404(conn, cur.lastrowid)), "duplicate": False}


@app.patch("/api/prescriptions/{rx_id}", dependencies=MANAGER)
def update_prescription(rx_id: int, body: PrescriptionPatch):
    with get_conn() as conn:
        fetch_prescription_or_404(conn, rx_id)
        fields = body.model_dump(exclude_unset=True)
        if "child_id" in fields:
            fetch_child_or_404(conn, fields["child_id"])
        if fields.get("assignee_id") is not None:
            fetch_user_or_404(conn, fields["assignee_id"])
        if "active" in fields:
            fields["active"] = int(fields["active"])
        for k in ("name", "pharmacy", "contact_name", "contact_phone", "notes"):
            if k in fields and fields[k] is not None:
                fields[k] = fields[k].strip()
        if fields:
            sets = ", ".join(f"{k} = ?" for k in fields)
            conn.execute(f"UPDATE prescriptions SET {sets} WHERE id = ?", (*fields.values(), rx_id))
        return refills.serialize_prescription(conn, fetch_prescription_or_404(conn, rx_id))


@app.delete("/api/prescriptions/{rx_id}", status_code=204, dependencies=MANAGER)
def delete_prescription(rx_id: int):
    """Removes the prescription and its pickup history. Use PATCH active=false to keep the history."""
    with get_conn() as conn:
        fetch_prescription_or_404(conn, rx_id)
        conn.execute("DELETE FROM prescriptions WHERE id = ?", (rx_id,))


@app.post("/api/prescriptions/{rx_id}/pickups", status_code=201, dependencies=MEMBER)
def log_pickup(rx_id: int, body: RefillPickupIn, actor: Actor = ACTOR):
    """A pickup restarts the clock and clears "called". A second pickup on the same day is refused
    unless `override` is set, since it's almost always a double entry."""
    with get_conn() as conn:
        fetch_prescription_or_404(conn, rx_id)
        day = body.picked_up_on.isoformat()
        same_day = conn.execute(
            "SELECT 1 FROM pickups WHERE prescription_id = ? AND picked_up_on = ?", (rx_id, day)
        ).fetchone()
        if same_day and not body.override:
            raise HTTPException(
                409, f"A pickup for this prescription is already logged on {day}. Send override=true if it really happened twice."
            )
        conn.execute(
            "INSERT INTO pickups (prescription_id, picked_up_on, picked_up_by, user_id, notes) VALUES (?, ?, ?, ?, ?)",
            (rx_id, day, actor.attribution(), actor.user_id, body.notes.strip()),
        )
        conn.execute("UPDATE prescriptions SET called_on = NULL, called_by = '', called_notes = '' WHERE id = ?", (rx_id,))
        return refills.serialize_prescription(conn, fetch_prescription_or_404(conn, rx_id))


@app.get("/api/prescriptions/{rx_id}/pickups", dependencies=MEMBER)
def pickup_history(rx_id: int, limit: int = 50):
    with get_conn() as conn:
        fetch_prescription_or_404(conn, rx_id)
        rows = conn.execute(
            "SELECT * FROM pickups WHERE prescription_id = ? ORDER BY picked_up_on DESC, id DESC LIMIT ?", (rx_id, limit)
        ).fetchall()
        return [dict(r) for r in rows]


@app.delete("/api/pickups/{pickup_id}", status_code=204, dependencies=MEMBER)
def delete_pickup(pickup_id: int):
    with get_conn() as conn:
        if not conn.execute("SELECT 1 FROM pickups WHERE id = ?", (pickup_id,)).fetchone():
            raise HTTPException(404, "Pickup not found")
        conn.execute("DELETE FROM pickups WHERE id = ?", (pickup_id,))


@app.post("/api/prescriptions/{rx_id}/called", dependencies=MEMBER)
def mark_called(rx_id: int, body: CalledIn, actor: Actor = ACTOR):
    """"I called the pharmacy / the doctor's office." Quiets refill_due for two days, then it comes
    back if nothing was picked up. Never quiets urgent."""
    with get_conn() as conn:
        fetch_prescription_or_404(conn, rx_id)
        conn.execute(
            "UPDATE prescriptions SET called_on = ?, called_by = ?, called_notes = ? WHERE id = ?",
            (date.today().isoformat(), actor.attribution(), body.notes.strip(), rx_id),
        )
        return refills.serialize_prescription(conn, fetch_prescription_or_404(conn, rx_id))


# ---------- Home upkeep (house-wide recurring maintenance) ----------


def serialize_upkeep(conn: sqlite3.Connection, i: sqlite3.Row) -> dict:
    last = conn.execute(
        "SELECT * FROM upkeep_logs WHERE item_id = ? ORDER BY done_on DESC, id DESC LIMIT 1",
        (i["id"],),
    ).fetchone()
    if last:
        due_date = date.fromisoformat(last["done_on"]) + timedelta(days=i["interval_days"])
        days_left = (due_date - date.today()).days
        soon_window = max(3, i["interval_days"] // 10)
        status = "due" if days_left <= 0 else "soon" if days_left <= soon_window else "ok"
    else:
        # Never logged: due now, so it surfaces until someone records it once.
        due_date = date.today()
        days_left = 0
        status = "due"
    due_on = due_date.isoformat()
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


@app.get("/api/upkeep", dependencies=MEMBER)
def list_upkeep(include_inactive: bool = False):
    with get_conn() as conn:
        where = "" if include_inactive else "WHERE active = 1"
        rows = conn.execute(f"SELECT * FROM upkeep_items {where} ORDER BY category, name").fetchall()
        return [serialize_upkeep(conn, i) for i in rows]


@app.post("/api/upkeep", status_code=201, dependencies=MEMBER)
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


@app.patch("/api/upkeep/{item_id}", dependencies=MEMBER)
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


@app.delete("/api/upkeep/{item_id}", status_code=204, dependencies=MEMBER)
def delete_upkeep(item_id: int):
    with get_conn() as conn:
        fetch_upkeep_or_404(conn, item_id)
        conn.execute("DELETE FROM upkeep_items WHERE id = ?", (item_id,))


@app.post("/api/upkeep/{item_id}/logs", status_code=201, dependencies=MEMBER)
def log_upkeep(item_id: int, body: UpkeepLogIn, actor: Actor = ACTOR):
    """A logged-in user's log entry is always recorded under their own name."""
    with get_conn() as conn:
        fetch_upkeep_or_404(conn, item_id)
        by = actor.name if actor.user_id else (body.done_by.strip() or actor.name)
        conn.execute(
            "INSERT INTO upkeep_logs (item_id, done_on, done_by, note, user_id) VALUES (?, ?, ?, ?, ?)",
            (item_id, body.done_on.isoformat(), by, body.note.strip(), actor.user_id),
        )
        return serialize_upkeep(conn, fetch_upkeep_or_404(conn, item_id))


@app.get("/api/upkeep/{item_id}/logs", dependencies=MEMBER)
def upkeep_history(item_id: int, limit: int = 50):
    with get_conn() as conn:
        fetch_upkeep_or_404(conn, item_id)
        rows = conn.execute(
            "SELECT * FROM upkeep_logs WHERE item_id = ? ORDER BY done_on DESC, id DESC LIMIT ?",
            (item_id, limit),
        ).fetchall()
        return [dict(r) for r in rows]


@app.delete("/api/upkeep-logs/{log_id}", status_code=204, dependencies=MEMBER)
def delete_upkeep_log(log_id: int):
    with get_conn() as conn:
        if not conn.execute("SELECT 1 FROM upkeep_logs WHERE id = ?", (log_id,)).fetchone():
            raise HTTPException(404, "Log entry not found")
        conn.execute("DELETE FROM upkeep_logs WHERE id = ?", (log_id,))


# ---------- Declutter list (drawers, closets, boxes to go through; done once, can be reset) ----------


def serialize_declutter(conn: sqlite3.Connection, s: sqlite3.Row) -> dict:
    room = conn.execute("SELECT name FROM rooms WHERE id = ?", (s["room_id"],)).fetchone() if s["room_id"] else None
    done_on = s["done_on"]
    return {
        "id": s["id"],
        "name": s["name"],
        "room_id": s["room_id"],
        "room": room["name"] if room else None,
        "notes": s["notes"],
        "done": done_on is not None,
        "done_on": done_on,
        "done_by": s["done_by"] or None,
        "days_since_done": (date.today() - date.fromisoformat(done_on)).days if done_on else None,
        "created_at": s["created_at"],
        "created_by": s["created_by"],
    }


def fetch_declutter_or_404(conn: sqlite3.Connection, spot_id: int) -> sqlite3.Row:
    s = conn.execute("SELECT * FROM declutter_spots WHERE id = ?", (spot_id,)).fetchone()
    if not s:
        raise HTTPException(404, "Declutter spot not found")
    return s


def find_declutter_duplicate(conn: sqlite3.Connection, name: str, room_id: int | None) -> sqlite3.Row | None:
    """Same-named spot in the same room ("junk drawer" in two rooms is two spots)."""
    rows = [dict(r) for r in conn.execute("SELECT * FROM declutter_spots WHERE room_id IS ?", (room_id,))]
    dup = _closest(name, rows)
    return fetch_declutter_or_404(conn, dup["id"]) if dup else None


def _insert_declutter(conn: sqlite3.Connection, body: DeclutterIn, actor: Actor) -> dict:
    if body.room_id is not None:
        fetch_room_or_404(conn, body.room_id)
    name = " ".join(body.name.split())
    dup = find_declutter_duplicate(conn, name, body.room_id)
    if dup:
        return {**serialize_declutter(conn, dup), "duplicate": True, "duplicate_of": dup["name"]}
    cur = conn.execute(
        "INSERT INTO declutter_spots (name, room_id, notes, created_at, created_by) VALUES (?, ?, ?, ?, ?)",
        (name, body.room_id, body.notes.strip(), now_iso(), actor.name),
    )
    return {**serialize_declutter(conn, fetch_declutter_or_404(conn, cur.lastrowid)), "duplicate": False}


@app.get("/api/declutter", dependencies=MEMBER)
def list_declutter(include_done: bool = False, room_id: int | None = None):
    """Open spots first (oldest first), then done ones most recent first."""
    with get_conn() as conn:
        where, params = [], []
        if not include_done:
            where.append("done_on IS NULL")
        if room_id is not None:
            where.append("room_id = ?")
            params.append(room_id)
        sql = "SELECT * FROM declutter_spots" + (" WHERE " + " AND ".join(where) if where else "")
        sql += " ORDER BY done_on IS NOT NULL, CASE WHEN done_on IS NULL THEN id END, done_on DESC, id DESC"
        return [serialize_declutter(conn, s) for s in conn.execute(sql, params)]


@app.post("/api/declutter", status_code=201, dependencies=MEMBER)
def create_declutter(body: Union[DeclutterIn, list[DeclutterIn]], actor: Actor = ACTOR):
    """One spot or a list of them in one transaction. A spot whose name closely matches an existing
    one in the same room comes back flagged `duplicate` instead of being added twice."""
    with get_conn() as conn:
        if isinstance(body, list):
            if not body:
                raise HTTPException(422, "Empty list")
            if len(body) > 200:
                raise HTTPException(422, "Too many spots in one request")
            return [_insert_declutter(conn, b, actor) for b in body]
        return _insert_declutter(conn, body, actor)


@app.patch("/api/declutter/{spot_id}", dependencies=MEMBER)
def update_declutter(spot_id: int, body: DeclutterPatch, actor: Actor = ACTOR):
    """`done: true` records who cleared it and when (today unless `done_on` given); `done: false`
    puts it back on the list to do again."""
    with get_conn() as conn:
        fetch_declutter_or_404(conn, spot_id)
        fields = body.model_dump(exclude_unset=True)
        done = fields.pop("done", None)
        done_on = fields.pop("done_on", None)
        if fields.get("room_id") is not None:
            fetch_room_or_404(conn, fields["room_id"])
        for k in ("name", "notes"):
            if k in fields and fields[k] is not None:
                fields[k] = " ".join(fields[k].split())
        if done is True:
            fields.update(done_on=(done_on or date.today()).isoformat(), done_by=actor.name, user_id=actor.user_id)
        elif done is False:
            fields.update(done_on=None, done_by="", user_id=None)
        if fields:
            sets = ", ".join(f"{k} = ?" for k in fields)
            conn.execute(f"UPDATE declutter_spots SET {sets} WHERE id = ?", (*fields.values(), spot_id))
        return serialize_declutter(conn, fetch_declutter_or_404(conn, spot_id))


@app.delete("/api/declutter/{spot_id}", status_code=204, dependencies=MEMBER)
def delete_declutter(spot_id: int):
    with get_conn() as conn:
        fetch_declutter_or_404(conn, spot_id)
        conn.execute("DELETE FROM declutter_spots WHERE id = ?", (spot_id,))


# ---------- Reminder settings (admin) ----------


@app.get("/api/admin/reminders", dependencies=ADMIN)
def get_reminder_settings():
    with get_conn() as conn:
        return _reminder_settings(conn)


def _reminder_settings(conn: sqlite3.Connection) -> dict:
    return {
        "reminder_email": notify.get_reminder_email(),
        "email_configured": notify.email_configured(),
        "refill_detail_in_notifications": refills.detail_enabled(conn),
        "due_refills": refills.due_refills(conn),
        "due_upkeep": notify.due_upkeep(),
        "pending_assignments": notify.pending_assignment_notifications(),
    }


@app.put("/api/admin/reminders", dependencies=ADMIN)
def set_reminder_settings(body: ReminderSettingsIn):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO settings (key, value) VALUES ('reminder_email', ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (body.reminder_email.strip(),),
        )
        if body.refill_detail_in_notifications is not None:
            refills.set_detail_enabled(conn, body.refill_detail_in_notifications)
        return _reminder_settings(conn)


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
# Readable by every member. The only medication data allowed here is the refills digest
# (child, prescription, days of supply left for anything refill_due/urgent) - see tests.


def task_days_until_due(t: dict) -> int | None:
    """Days until a task is due (negative = overdue). None for one-off tasks with no due date."""
    if t["frequency_days"] is None:
        if t.get("due_on") and not t.get("done"):
            return (date.fromisoformat(t["due_on"]) - date.today()).days
        return None
    if t["last_completed_at"] is None:
        return 0
    last = datetime.fromisoformat(t["last_completed_at"]).date()
    return (last + timedelta(days=t["frequency_days"]) - date.today()).days


def _status_task(t: dict, days: int) -> dict:
    return {
        "id": t["id"],
        "title": t["title"],
        "room_slug": t["room_slug"],
        "room_name": t["room_name"],
        "assignee": t["assignee"],
        "days_until_due": days,
        "last_completed_at": t["last_completed_at"],
    }


@app.get("/api/status", dependencies=MEMBER)
def status_summary():
    overdue, due_today, due_week = [], [], []
    with get_conn() as conn:
        tasks = conn.execute(ACTIVE_TASKS_SQL).fetchall()
        for row in tasks:
            t = serialize_task(conn, row)
            days = task_days_until_due(t)
            if days is None:
                continue
            entry = _status_task(t, days)
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
        due = refills.status_section(conn)
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
        "refills": due,
    }


@app.get("/api/health")
def health():
    return {"ok": True}


# ---------- MCP server: /mcp plus OAuth endpoints (/authorize, /token, /register, /.well-known/*) ----------
# Its routes go onto this app's router so they sit at the root path, ahead of the SPA catch-all.

from .mcp_server import build_mcp  # noqa: E402  (needs the functions above)

mcp = build_mcp()
mcp_http = mcp.http_app(path="/mcp", stateless_http=True, json_response=True)
app.router.routes.extend(mcp_http.routes)
for mw in mcp_http.user_middleware:
    app.add_middleware(mw.cls, *mw.args, **mw.kwargs)


# ---------- Static frontend (production) ----------

if STATIC_DIR.is_dir():
    app.mount("/assets", StaticFiles(directory=STATIC_DIR / "assets"), name="assets")

    @app.get("/{full_path:path}")
    def spa(full_path: str):
        candidate = STATIC_DIR / full_path
        if full_path and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(STATIC_DIR / "index.html")
