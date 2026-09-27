"""MCP server (Streamable HTTP at /mcp) so Claude can read and write the home app.

Runs inside the FastAPI process and calls the same functions as the REST API, as the
`connector` role: rooms, tasks, people's lists, upkeep, pantry, shopping, children's sizes and
needs, prescription refills and status. Every tool must be listed in tests/test_mcp.py EXPECTED_TOOLS.

Every identifier argument accepts an id or a spoken name; see matching.py. When several
rows plausibly match, a tool returns {"needs_disambiguation": true, "candidates": [...]}
and changes nothing. Writes accept an `idempotency_key`; a repeat within a few minutes
returns the first result without doing anything again.
"""

import json
import re
import time
from datetime import date, datetime, timedelta, timezone
from typing import Annotated, Any

from fastapi import Response
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from pydantic import Field

from . import main as core
from . import kids, oauth, refills
from .db import get_conn
from .matching import Ambiguous, Match, NoMatch, resolve, similarity

IDEMPOTENCY_WINDOW = 10 * 60
DEFAULT_ACTOR = "Claude"

Id = Annotated[str, Field(description="Name as someone would say it, or the numeric id.")]
IdemKey = Annotated[
    str | None,
    Field(description="Optional. Any string; a repeat call with the same key within 10 minutes is ignored."),
]


def build_mcp() -> FastMCP:
    return FastMCP(
        name="Home",
        instructions=(
            "Home maintenance, upkeep, pantry, shopping, the kids' sizes/needs and prescription refills "
            "for one household. Refer to things by "
            "name; if a tool answers needs_disambiguation, ask the person which one they meant and "
            "call it again with the exact name or id."
        ),
        auth=oauth.SqliteOAuthProvider(oauth.public_base_url()),
        tools=[
            get_home_status,
            list_rooms,
            add_room,
            rename_room,
            archive_room,
            list_tasks,
            add_task,
            complete_task,
            list_upkeep,
            log_upkeep_done,
            list_pantry,
            add_pantry_items,
            update_pantry_item,
            remove_pantry_item,
            list_shopping,
            add_to_shopping,
            add_to_list,
            list_for_person,
            list_children,
            get_child,
            set_size,
            list_sizes,
            add_need,
            mark_have,
            list_needs,
            seasonal_check,
            list_refills,
            log_pickup,
            mark_called,
            list_prescriptions,
            add_prescription,
            find,
        ],
    )


# ---------- helpers ----------


def _idempotent(key: str | None, tool: str, run) -> Any:
    if not key:
        return run()
    with get_conn() as conn:
        conn.execute("DELETE FROM idempotency_keys WHERE created_at < ?", (time.time() - IDEMPOTENCY_WINDOW,))
        row = conn.execute("SELECT response FROM idempotency_keys WHERE key = ?", (f"{tool}:{key}",)).fetchone()
    if row:
        return {**json.loads(row["response"]), "replayed": True}
    result = run()
    if isinstance(result, dict) and result.get("needs_disambiguation"):
        return result
    with get_conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO idempotency_keys (key, tool, response, created_at) VALUES (?, ?, ?, ?)",
            (f"{tool}:{key}", tool, json.dumps(result, default=str), time.time()),
        )
    return result


def _names(items: list[dict], key: str = "name") -> str:
    return ", ".join(i[key] for i in items)


def _disambiguation(kind: str, query: str, candidates: list[dict]) -> dict:
    return {
        "needs_disambiguation": True,
        "message": f"More than one {kind} matches '{query}'. Which one?",
        "candidates": candidates,
    }


def _no_match(kind: str, query: str, nearest: list[dict], all_names: list[str] | None = None) -> ToolError:
    if all_names is not None:
        return ToolError(f"No {kind} called '{query}'. {kind.capitalize()}s are: {', '.join(all_names)}.")
    near = ", ".join(n["name"] for n in nearest) or "nothing similar"
    return ToolError(f"No {kind} called '{query}'. Closest names: {near}.")


def _room_brief(r: dict) -> dict:
    return {"id": r["id"], "name": r["name"], "slug": r["slug"]}


def _all_rooms(conn) -> list[dict]:
    return [
        core.serialize_room(conn, r, with_tasks=False)
        for r in conn.execute("SELECT * FROM rooms WHERE active = 1 ORDER BY sort_order, id")
    ]


def _resolve_room(conn, room: str) -> dict:
    rooms = _all_rooms(conn)
    res = resolve(room, rooms)
    if isinstance(res, Match):
        return res.item
    if isinstance(res, Ambiguous):
        raise _AmbiguousError(_disambiguation("room", room, [_room_brief(c) for c in res.candidates]))
    raise _no_match("room", room, res.nearest, [r["name"] for r in rooms])


class _AmbiguousError(Exception):
    """Carries a needs_disambiguation payload out of a helper; tools return it as a normal result."""

    def __init__(self, payload: dict):
        self.payload = payload


def _all_tasks(conn, include_done: bool = False) -> list[dict]:
    out = []
    for row in conn.execute(core.ACTIVE_TASKS_SQL):
        t = core.serialize_task(conn, row)
        if t["done"] and not include_done:
            continue
        t["days_until_due"] = core.task_days_until_due(t)
        t["due_on"] = (date.today() + timedelta(days=t["days_until_due"])).isoformat() if t["days_until_due"] is not None else None
        out.append(t)
    return out


def _task_brief(t: dict) -> dict:
    return {
        "id": t["id"],
        "title": t["title"],
        "room": t["room_name"],
        "assignee": t["assignee"],
        "status": t["status"],
        "overdue": t["status"] == "overdue",
        "due_on": t["due_on"],
        "frequency_days": t["frequency_days"],
        "done": t["done"],
        "created_at": t["created_at"],
        "last_completed_at": t["last_completed_at"],
        "last_completed_by": t["last_completed_by"],
        "notes": [n["body"] for n in t["notes"]],
    }


def _task_room_label(t: dict) -> str:
    return t["room_name"] or (f"{t['assignee']}'s list" if t["assignee"] else "no room")


def _resolve_task(conn, task: str) -> dict:
    tasks = _all_tasks(conn)
    res = resolve(task, tasks, name_key="title")
    if isinstance(res, NoMatch) or isinstance(res, Ambiguous):
        # "bathroom mirror in the pink bathroom": try title + room together
        for t in tasks:
            t["_full"] = f"{t['title']} {_task_room_label(t)}"
        res2 = resolve(task, tasks, name_key="_full")
        if isinstance(res2, Match) or (isinstance(res2, Ambiguous) and isinstance(res, NoMatch)):
            res = res2
    if isinstance(res, Match):
        return res.item
    if isinstance(res, Ambiguous):
        raise _AmbiguousError(_disambiguation("task", task, [_task_brief(c) for c in res.candidates]))
    raise _no_match("task", task, [{"name": f"{t['title']} ({_task_room_label(t)})"} for t in res.nearest])


def _all_people(conn) -> list[dict]:
    return [core.auth.public_user(r) for r in conn.execute("SELECT * FROM users WHERE active = 1 ORDER BY id")]


class _UnknownPerson(Exception):
    """Not an error to Claude: the tool answers with the list of people and changes nothing."""

    def __init__(self, person: str, names: list[str]):
        self.payload = {
            "created": False,
            "unknown_person": person,
            "people": names,
            "message": f"Nobody here is called '{person}'. The people are: {', '.join(names)}. Who did you mean?",
        }


def _resolve_person(conn, person: str) -> dict:
    """First name, case-insensitive. No fuzzy guessing: an unknown name lists everyone instead."""
    people = _all_people(conn)
    wanted = (person or "").strip().lower()
    for p in people:
        if p["name"].lower() == wanted or (wanted.isdigit() and p["id"] == int(wanted)):
            return p
    raise _UnknownPerson(person, [p["name"] for p in people])


def _all_upkeep(conn) -> list[dict]:
    return [core.serialize_upkeep(conn, i) for i in conn.execute("SELECT * FROM upkeep_items WHERE active = 1 ORDER BY name")]


def _resolve_upkeep(conn, item: str) -> dict:
    items = _all_upkeep(conn)
    res = resolve(item, items)
    if isinstance(res, Match):
        return res.item
    if isinstance(res, Ambiguous):
        raise _AmbiguousError(_disambiguation("upkeep item", item, res.candidates))
    raise _no_match("upkeep item", item, res.nearest)


def _all_pantry(conn) -> list[dict]:
    return [core.serialize_pantry(r) for r in conn.execute("SELECT * FROM pantry_items ORDER BY name")]


def _pantry_brief(p: dict) -> dict:
    return {
        "id": p["id"],
        "name": p["name"],
        "location": p["category"],
        "quantity": p["quantity"],
        "low": p["low"],
        "par_level": p["par_level"],
        "expires_on": p["expires_on"],
        "days_to_expiry": p["days_to_expiry"],
    }


def _resolve_pantry(conn, item: str) -> dict:
    items = _all_pantry(conn)
    res = resolve(item, items)
    if isinstance(res, Match):
        return res.item
    if isinstance(res, Ambiguous):
        raise _AmbiguousError(_disambiguation("pantry item", item, [_pantry_brief(c) for c in res.candidates]))
    raise _no_match("pantry item", item, res.nearest)


_RECURRENCE = {
    "daily": 1, "every day": 1, "weekly": 7, "every week": 7, "biweekly": 14, "fortnightly": 14,
    "every other week": 14, "every two weeks": 14, "monthly": 30, "every month": 30, "bimonthly": 60,
    "quarterly": 90, "every quarter": 90, "twice a year": 182, "semiannually": 182, "yearly": 365,
    "annually": 365, "every year": 365, "once": None, "one time": None, "one-off": None, "none": None,
}
_UNIT_DAYS = {"day": 1, "week": 7, "month": 30, "year": 365}


def _parse_recurrence(text: str | None) -> int | None:
    if text is None or not str(text).strip():
        return None
    s = str(text).strip().lower()
    if s.isdigit():
        return int(s) or None
    if s in _RECURRENCE:
        return _RECURRENCE[s]
    m = re.match(r"(?:every\s+)?(\d+|a|an|one|two|three|four|five|six)?\s*(day|week|month|year)s?", s)
    if m:
        words = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6}
        n = m.group(1)
        count = words.get(n, None) if n and not n.isdigit() else int(n) if n else 1
        return count * _UNIT_DAYS[m.group(2)]
    raise ToolError(
        f"I don't understand the recurrence '{text}'. Say something like daily, weekly, every 2 weeks, "
        "monthly, every 3 months, yearly, or a number of days."
    )


def _parse_date(text: str | None, what: str) -> date | None:
    if text is None or not str(text).strip():
        return None
    s = str(text).strip().lower()
    if s == "today":
        return date.today()
    if s == "yesterday":
        return date.today() - timedelta(days=1)
    if s == "tomorrow":
        return date.today() + timedelta(days=1)
    try:
        return date.fromisoformat(s)
    except ValueError:
        raise ToolError(f"{what} should be a date like 2026-03-14 (or today / yesterday / tomorrow), not '{text}'.")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _run(fn):
    """Turn a helper's disambiguation into a normal result and HTTP errors into plain sentences."""
    try:
        return fn()
    except (_AmbiguousError, _UnknownPerson) as e:
        return e.payload
    except core.HTTPException as e:
        raise ToolError(str(e.detail))


_ACTOR = core.CONNECTOR_ACTOR


# ---------- tools ----------


def get_home_status() -> dict:
    """What needs attention around the house right now: overdue tasks, tasks due today and this
    week, upkeep jobs that are due, pantry items running low, and food expiring within a week.
    Start here when someone asks "what needs doing" or "what are we out of"."""
    return core.status_summary()


def list_rooms() -> list[dict]:
    """The rooms in the house, each with how many tasks it has and how many are due."""
    with get_conn() as conn:
        return [
            {**_room_brief(r), "task_count": r["task_count"], "due_count": r["due_count"], "note_count": r["note_count"]}
            for r in _all_rooms(conn)
        ]


def add_room(
    name: Annotated[str, Field(description="The room's name, e.g. 'Dining Room'.")],
    icon: Annotated[str | None, Field(description="Icon name shown in the app (home, bed, bath, utensils, car, ...). Optional.")] = None,
    idempotency_key: IdemKey = None,
) -> dict:
    """Add a new room to the house, like "add a dining room". If a room with (nearly) the same
    name already exists, nothing is created and that room is returned flagged as a duplicate."""

    def go():
        with get_conn() as conn:
            existing = core.find_similar_room(conn, name)
            if existing:
                return {
                    "created": False,
                    "duplicate": True,
                    "room": _room_brief(dict(existing)),
                    "message": f"There's already a room called {existing['name']}; nothing was added.",
                }
        room = core.create_room(core.RoomIn(name=name, icon=icon or "home"), Response())
        return {"created": True, "duplicate": False, "room": _room_brief(room), "message": f"Added the room {room['name']}."}

    return _idempotent(idempotency_key, "add_room", lambda: _run(go))


def rename_room(
    room: Id,
    new_name: Annotated[str, Field(description="The new name.")],
) -> dict:
    """Rename a room, like "rename the little boys room to the nursery". Keeps the same room and
    all its task history; NFC tags written with the old address keep working."""

    def go():
        with get_conn() as conn:
            r = _resolve_room(conn, room)
        updated = core.update_room(r["id"], core.RoomPatch(name=new_name))
        return {
            "renamed": True,
            "room": _room_brief(updated),
            "old_name": r["name"],
            "old_slug": r["slug"],
            "message": f"Renamed {r['name']} to {updated['name']}. Tags pointing at /r/{r['slug']} still work.",
        }

    return _run(go)


def archive_room(room: Id) -> dict:
    """Retire a room, like "we don't use the guest room anymore". It disappears from the room
    list and from what's due, but its tasks and history are kept."""

    def go():
        with get_conn() as conn:
            r = _resolve_room(conn, room)
        updated = core.update_room(r["id"], core.RoomPatch(active=False))
        return {"archived": True, "room": _room_brief(updated), "message": f"Archived {updated['name']}; its history is kept."}

    return _run(go)


def list_tasks(
    room: Annotated[str | None, Field(description="Only this room (name or id). Leave empty for every room.")] = None,
    status: Annotated[
        str | None,
        Field(description="Filter: 'due' (due or overdue), 'overdue', or 'ok' (not due yet). Empty for all."),
    ] = None,
    due_before: Annotated[str | None, Field(description="Only tasks due on or before this date, YYYY-MM-DD.")] = None,
    assignee: Annotated[str | None, Field(description="Only tasks on this person's list (first name, e.g. 'Susan').")] = None,
) -> dict | list[dict]:
    """The cleaning, maintenance and errand tasks, with when each is next due, who it's assigned
    to and who did it last. Filter by room, by status, by a due date, or by the person it's
    assigned to."""

    def go():
        with get_conn() as conn:
            tasks = _all_tasks(conn)
            if room:
                r = _resolve_room(conn, room)
                tasks = [t for t in tasks if t["room_id"] == r["id"]]
            if assignee:
                p = _resolve_person(conn, assignee)
                tasks = [t for t in tasks if t["assignee_id"] == p["id"]]
        if status:
            s = status.lower()
            if s == "due":
                tasks = [t for t in tasks if t["status"] != "ok"]
            elif s in ("overdue", "ok"):
                tasks = [t for t in tasks if t["status"] == s]
            else:
                raise ToolError(f"Unknown status '{status}'. Use due, overdue or ok.")
        if due_before:
            cutoff = _parse_date(due_before, "due_before")
            tasks = [t for t in tasks if t["due_on"] is not None and date.fromisoformat(t["due_on"]) <= cutoff]
        return [_task_brief(t) for t in tasks]

    return _run(go)


def _create_task(conn, title: str, room: str | None, due_on: str | None, recurrence: str | None, notes: str | None, assignee: str | None) -> dict:
    freq = _parse_recurrence(recurrence)
    first_due = _parse_date(due_on, "due_on")
    r = _resolve_room(conn, room) if room else None
    p = _resolve_person(conn, assignee) if assignee else None
    t = core.create_task(
        core.TaskIn(
            room_id=r["id"] if r else None,
            title=title,
            description=notes or "",
            frequency_days=freq,
            assignee_id=p["id"] if p else None,
            due_on=first_due if not freq else None,
        ),
        actor=_ACTOR,
    )
    if first_due and freq:
        # a recurring task is due `frequency_days` after its last completion, so backdate one
        anchor = datetime.combine(first_due - timedelta(days=freq), datetime.min.time(), tzinfo=timezone.utc)
        conn.execute(
            "INSERT INTO completions (task_id, completed_by, completed_at) VALUES (?, ?, ?)",
            (t["id"], "(scheduled)", anchor.isoformat(timespec="seconds")),
        )
    return next(x for x in _all_tasks(conn) if x["id"] == t["id"])


def add_task(
    title: Annotated[str, Field(description="What needs doing, e.g. 'Wipe down the baseboards'.")],
    room: Annotated[str | None, Field(description="Which room (name or id). Leave empty for an errand that isn't about a room.")] = None,
    due_on: Annotated[str | None, Field(description="When it's due, YYYY-MM-DD (or today/tomorrow). For a recurring task, when it's first due.")] = None,
    recurrence: Annotated[
        str | None,
        Field(description="How often it repeats: daily, weekly, every 2 weeks, monthly, every 3 months, yearly, or a number of days. Empty = one-off."),
    ] = None,
    notes: Annotated[str | None, Field(description="Extra detail or instructions.")] = None,
    assignee: Annotated[str | None, Field(description="Whose list it goes on (first name, e.g. 'Susan'). Empty = nobody in particular.")] = None,
    idempotency_key: IdemKey = None,
) -> dict:
    """Add a new task, like "add 'clean the ceiling fan' to the girls room, monthly" or "Vanessa
    should return the library books by Friday". Assigning it to someone puts it on their list and
    notifies them."""

    def go():
        if not room and not assignee:
            raise ToolError("Give a room for the task, or a person to assign it to (or both).")
        with get_conn() as conn:
            task = _create_task(conn, title, room, due_on, recurrence, notes, assignee)
        out = {"created": True, "task": _task_brief(task)}
        if task["assignee"]:
            out["message"] = f"Added '{task['title']}' to {task['assignee']}'s list and let them know."
        return out

    return _idempotent(idempotency_key, "add_task", lambda: _run(go))


def complete_task(
    task: Id,
    note: Annotated[str | None, Field(description="Anything to remember, e.g. 'need more grout cleaner'. Mentioning buying/needing something puts it on the shopping list.")] = None,
    done_by: Annotated[str | None, Field(description="Who did it. Defaults to 'Claude'.")] = None,
    idempotency_key: IdemKey = None,
) -> dict:
    """Mark a task done right now, like "I finished the pink bathroom mirror" or "mopped the kitchen".
    Records who did it and when, and schedules the next time it's due."""

    def go():
        with get_conn() as conn:
            t = _resolve_task(conn, task)
            core.complete_task(t["id"], core.CompleteIn(completed_by=(done_by or DEFAULT_ACTOR).strip() or DEFAULT_ACTOR), actor=_ACTOR)
            if note:
                needs = bool(re.search(r"\b(buy|need|out of|order|purchase|pick up|more)\b", note.lower()))
                core.add_note(t["id"], core.NoteIn(author=(done_by or DEFAULT_ACTOR), body=note, needs_purchase=needs))
            fresh = next(x for x in _all_tasks(conn) if x["id"] == t["id"])
        return {
            "completed": True,
            "task": _task_brief(fresh),
            "completed_at": fresh["last_completed_at"],
            "next_due_on": fresh["due_on"],
        }

    return _idempotent(idempotency_key, "complete_task", lambda: _run(go))


def list_upkeep(
    category: Annotated[str | None, Field(description="Only this category, e.g. HVAC, Car, Exterior, Safety.")] = None,
    due_only: Annotated[bool, Field(description="Only items that are due or overdue.")] = False,
) -> list[dict]:
    """House-wide upkeep jobs that aren't tied to a room: furnace filter, car oil change, gutters,
    smoke detector batteries... with when each was last done and when it's next due."""
    with get_conn() as conn:
        items = _all_upkeep(conn)
    if category:
        c = category.lower()
        items = [i for i in items if similarity(c, i["category"]) >= 0.72] or [i for i in items if c in i["category"].lower()]
    if due_only:
        items = [i for i in items if i["status"] == "due"]
    return items


def log_upkeep_done(
    item: Id,
    done_on: Annotated[str | None, Field(description="Date it was done, YYYY-MM-DD. Defaults to today.")] = None,
    note: Annotated[str | None, Field(description="Anything worth remembering, e.g. mileage or filter size.")] = None,
    done_by: Annotated[str | None, Field(description="Who did it. Defaults to 'Claude'.")] = None,
    idempotency_key: IdemKey = None,
) -> dict:
    """Record that an upkeep job got done, like "changed the furnace filter today" or "the oil was
    changed on the 3rd". Moves its next due date forward by the item's interval."""

    def go():
        when = _parse_date(done_on, "done_on") or date.today()
        with get_conn() as conn:
            i = _resolve_upkeep(conn, item)
            fresh = core.log_upkeep(
                i["id"], core.UpkeepLogIn(done_on=when, done_by=(done_by or DEFAULT_ACTOR), note=note or ""), actor=_ACTOR
            )
        return {"logged": True, "item": fresh}

    return _idempotent(idempotency_key, "log_upkeep_done", lambda: _run(go))


# ---------- prescription refills ----------


def _all_children(conn) -> list[dict]:
    return [core.serialize_child(c) for c in conn.execute("SELECT * FROM children WHERE active = 1 ORDER BY name")]


def _resolve_child(conn, child: str) -> dict:
    kids = _all_children(conn)
    res = resolve(child, kids)
    if isinstance(res, Match):
        return res.item
    if isinstance(res, Ambiguous):
        raise _AmbiguousError(_disambiguation("child", child, [{"id": c["id"], "name": c["name"]} for c in res.candidates]))
    raise _no_match("child", child, res.nearest, [c["name"] for c in kids])


def _rx_brief(p: dict) -> dict:
    return {
        "id": p["id"],
        "child": p["child"],
        "name": p["name"],
        "refill_status": p["refill_status"],
        "days_since_pickup": p["days_since_pickup"],
        "days_of_supply_left": p["days_of_supply_left"],
        "last_picked_up_on": p["last_picked_up_on"],
        "pharmacy": p["pharmacy"],
        "contact_name": p["contact_name"],
        "contact_phone": p["contact_phone"],
        "assignee": p["assignee"],
    }


def _resolve_prescription(conn, child: str, prescription: str | None) -> dict:
    """The child is resolved first, then the prescription within that child's list. A child with
    several prescriptions and no prescription named is a disambiguation, never a guess."""
    kid = _resolve_child(conn, child)
    rxs = [p for p in refills.active_prescriptions(conn) if p["child_id"] == kid["id"]]
    if not rxs:
        raise ToolError(f"{kid['name']} has no active prescriptions.")
    if not prescription:
        if len(rxs) == 1:
            return rxs[0]
        raise _AmbiguousError(_disambiguation("prescription", f"{kid['name']}'s", [_rx_brief(p) for p in rxs]))
    res = resolve(prescription, rxs)
    if isinstance(res, Match):
        return res.item
    if isinstance(res, Ambiguous):
        raise _AmbiguousError(_disambiguation("prescription", prescription, [_rx_brief(p) for p in res.candidates]))
    raise _no_match("prescription", prescription, res.nearest, [f"{p['name']} ({kid['name']})" for p in rxs])


def list_refills(
    status: Annotated[
        str | None,
        Field(description="refill_due, urgent, called_waiting, ok, or 'all'. Default: refill_due and urgent only."),
    ] = None,
) -> dict:
    """Which of the kids' prescriptions need a refill call right now. Controlled prescriptions can't be
    refilled before day 28 and a 30-day supply runs out at day 30, so this is the list to act on;
    urgent means the supply is already gone. Items stay on this list until a pickup is logged."""
    with get_conn() as conn:
        items = refills.active_prescriptions(conn)
    wanted = (status or "").strip().lower().replace(" ", "_")
    if wanted in ("", "due"):
        items = [p for p in items if p["refill_status"] in refills.DUE_STATUSES]
    elif wanted != "all":
        items = [p for p in items if p["refill_status"] == wanted]
    items.sort(key=lambda p: (p["days_of_supply_left"] if p["days_of_supply_left"] is not None else 999, p["child"]))
    urgent = sum(1 for p in items if p["refill_status"] == "urgent")
    return {
        "items": [_rx_brief(p) for p in items],
        "urgent_count": urgent,
        "message": (
            "No refills need attention right now."
            if not items
            else f"{len(items)} prescription(s) need attention" + (f", {urgent} already out of supply." if urgent else ".")
        ),
    }


def log_pickup(
    child: Annotated[str, Field(description="Whose prescription: the child's first name.")],
    prescription: Annotated[
        str | None, Field(description="Which prescription (name or id). Can be left out only if the child has exactly one.")
    ] = None,
    picked_up_on: Annotated[str | None, Field(description="Date it was picked up, YYYY-MM-DD. Defaults to today.")] = None,
    notes: Annotated[str | None, Field(description="Anything worth remembering.")] = None,
    override: Annotated[bool, Field(description="Set true to log a second pickup on the same day on purpose.")] = False,
    idempotency_key: IdemKey = None,
) -> dict:
    """Record that a prescription was picked up, like "I picked up Henry's meds today". Restarts the
    28-day clock and clears any 'called' state. If the child has more than one prescription and none
    is named, asks which one instead of guessing."""

    def go():
        when = _parse_date(picked_up_on, "picked_up_on") or date.today()
        with get_conn() as conn:
            rx = _resolve_prescription(conn, child, prescription)
        fresh = core.log_pickup(rx["id"], core.RefillPickupIn(picked_up_on=when, notes=notes or "", override=override), actor=_ACTOR)
        return {
            "logged": True,
            "prescription": _rx_brief(fresh),
            "message": f"Logged {fresh['name']} for {fresh['child']} picked up {when.isoformat()}. Next refill window opens in {fresh['refill_after_days'] - fresh['days_since_pickup']} day(s).",
        }

    return _idempotent(idempotency_key, "log_pickup", lambda: _run(go))


def mark_called(
    child: Annotated[str, Field(description="Whose prescription: the child's first name.")],
    prescription: Annotated[str | None, Field(description="Which prescription (name or id). Optional if the child has one.")] = None,
    notes: Annotated[str | None, Field(description="e.g. 'pharmacy says ready Thursday'.")] = None,
) -> dict:
    """Note that someone already called the pharmacy or the prescriber about a refill, like "I called
    about Henry's refill". Quiets the reminder for two days; it comes back if no pickup is logged.
    Does not quiet an urgent (out of supply) reminder."""

    def go():
        with get_conn() as conn:
            rx = _resolve_prescription(conn, child, prescription)
        fresh = core.mark_called(rx["id"], core.CalledIn(notes=notes or ""), actor=_ACTOR)
        quiet = fresh["refill_status"] == "called_waiting"
        return {
            "called": True,
            "prescription": _rx_brief(fresh),
            "message": f"Noted the call about {fresh['name']} for {fresh['child']}. "
            + ("The reminder is quiet for two days." if quiet else "It's still urgent, so the reminder stays on." if fresh["refill_status"] == "urgent" else ""),
        }

    return _run(go)


def list_prescriptions(
    child: Annotated[str | None, Field(description="Only this child's prescriptions. Leave empty for everyone.")] = None,
) -> list[dict]:
    """All the kids' active prescriptions with pharmacy, who to call, days since pickup and refill
    status. For "what does Henry take" or "who do we call about Ava's refill"."""

    def go():
        with get_conn() as conn:
            items = refills.active_prescriptions(conn)
            if child:
                kid = _resolve_child(conn, child)
                items = [p for p in items if p["child_id"] == kid["id"]]
        return [_rx_brief(p) for p in items]

    return _run(go)


def add_prescription(
    child: Annotated[str, Field(description="The child's first name. A new child is created if unknown.")],
    name: Annotated[str, Field(description="The prescription name as written on the bottle.")],
    pharmacy: Annotated[str | None, Field(description="Where it's filled.")] = None,
    contact_name: Annotated[str | None, Field(description="Who to call about refills (pharmacy or prescriber's office).")] = None,
    contact_phone: Annotated[str | None, Field(description="Their phone number.")] = None,
    days_supply: Annotated[int | None, Field(description="Days each fill lasts. Default 30.")] = None,
    refill_after_days: Annotated[int | None, Field(description="Earliest day a refill is allowed. Default 28.")] = None,
    idempotency_key: IdemKey = None,
) -> dict:
    """Start tracking a prescription, like "add Henry's Focalin, 30-day supply, refill after 28 days,
    call Dr. Lee's office at 555-0100". If the child isn't known yet they are added."""

    def go():
        with get_conn() as conn:
            kids = _all_children(conn)
            res = resolve(child, kids)
            if isinstance(res, Ambiguous):
                raise _AmbiguousError(_disambiguation("child", child, [{"id": c["id"], "name": c["name"]} for c in res.candidates]))
            kid = res.item if isinstance(res, Match) else None
            existing = (
                [p for p in refills.active_prescriptions(conn) if p["child_id"] == kid["id"] and similarity(name, p["name"]) >= 0.9]
                if kid
                else []
            )
        if kid is None:
            kid = core.create_child(core.ChildIn(name=child.strip()), Response())
        if existing:
            return {
                "created": False,
                "duplicate": True,
                "prescription": _rx_brief(existing[0]),
                "message": f"{kid['name']} already has {existing[0]['name']}; nothing was added.",
            }
        fresh = core.create_prescription(
            core.PrescriptionIn(
                child_id=kid["id"],
                name=name,
                pharmacy=pharmacy or "",
                contact_name=contact_name or "",
                contact_phone=contact_phone or "",
                days_supply=days_supply or 30,
                refill_after_days=refill_after_days or 28,
            ),
            Response(),
        )
        return {
            "created": True,
            "duplicate": False,
            "prescription": _rx_brief(fresh),
            "message": f"Added {fresh['name']} for {fresh['child']}. Log the first pickup so the refill clock starts.",
        }

    return _idempotent(idempotency_key, "add_prescription", lambda: _run(go))


# ---------- children: sizes and needs ----------


def _size_brief(s: dict) -> dict:
    return {
        "child": s["child"],
        "category": s["category"],
        "value": s["value"],
        "notes": s["notes"],
        "updated_by": s["updated_by"],
        "age_days": s["age_days"],
        "stale": s["stale"],
        "note": f"Recorded {s['age_days']} days ago; kids grow, so check before buying." if s["stale"] else None,
    }


def _need_brief(n: dict) -> dict:
    return {
        "id": n["id"],
        "child": n["child"],
        "category": n["category"],
        "season": n["season"],
        "status": n["status"],
        "notes": n["notes"],
        "open_for_days": n["open_for_days"],
        "on_shopping_list": n["shopping_item_id"] is not None,
        "created_by": n["created_by"],
        "resolved_by": n["resolved_by"] or None,
    }


def _resolve_category(conn, category: str, must_exist: bool = False, prefer_existing: bool = False) -> str:
    """Writing (default): fold "shoe"/"Shoes" together, otherwise keep the words as said (so "pants"
    is not turned into "Snow Pants"). Reading (must_exist): fuzzy like everything else, asking when
    several fit. prefer_existing: fuzzy when something matches, otherwise take the words as said."""
    if not must_exist and not prefer_existing:
        return kids.canonical_category(conn, category)
    known = [{"name": c} for c in kids.known_categories(conn)]
    res = resolve(category, known)
    if isinstance(res, Match):
        return res.item["name"]
    if isinstance(res, Ambiguous):
        raise _AmbiguousError(_disambiguation("category", category, [{"category": c["name"]} for c in res.candidates]))
    if prefer_existing:
        return kids.canonical_category(conn, category)
    raise _no_match("category", category, res.nearest, [c["name"] for c in known])


def list_children() -> list[dict]:
    """The kids in the house, with how many sizes, open needs and prescriptions each one has.
    Use it to learn the children's names before asking about sizes, needs or refills."""
    with get_conn() as conn:
        out = []
        for c in _all_children(conn):
            out.append(
                {
                    "id": c["id"],
                    "name": c["name"],
                    "sizes_recorded": len(kids.sizes(conn, c["id"])),
                    "open_needs": len(kids.needs(conn, c["id"])),
                    "prescriptions": len([p for p in refills.active_prescriptions(conn) if p["child_id"] == c["id"]]),
                }
            )
        return out


def get_child(child: Annotated[str, Field(description="The child's first name (or id).")]) -> dict:
    """Everything about one child: their current sizes (each with how old the value is), what they
    still need, and how many prescriptions they have. For "what size is Henry" or "what does Ava need"."""

    def go():
        with get_conn() as conn:
            kid = _resolve_child(conn, child)
            full = core.get_child(kid["id"])
        return {
            "id": full["id"],
            "name": full["name"],
            "sizes": [_size_brief(s) for s in full["sizes"]],
            "needs": [_need_brief(n) for n in full["needs"]],
            "prescription_count": full["prescription_count"],
        }

    return _run(go)


def set_size(
    child: Annotated[str, Field(description="The child's first name.")],
    category: Annotated[str, Field(description="What kind of size: shoes, pants, shirts, coat, diapers, hat...")],
    value: Annotated[str, Field(description="The size as you'd say it: '13 toddler', '5T', 'youth M', 'size 4'.")],
    notes: Annotated[str | None, Field(description="e.g. 'runs small in Nike'.")] = None,
    idempotency_key: IdemKey = None,
) -> dict:
    """Record a child's current size in one category, like "Henry is a 13 in shoes now". Replaces the
    old value for that category and notes who updated it and when, so later reads can say how old
    the size is."""

    def go():
        with get_conn() as conn:
            kid = _resolve_child(conn, child)
            cat = _resolve_category(conn, category)
        fresh = core.set_child_size(kid["id"], core.SizeIn(category=cat, value=value, notes=notes or ""), actor=_ACTOR)
        return {"saved": True, "size": _size_brief(fresh), "message": f"{kid['name']}'s {cat.lower()} size is now {fresh['value']}."}

    return _idempotent(idempotency_key, "set_size", lambda: _run(go))


def list_sizes(
    category: Annotated[str | None, Field(description="Only one kind, e.g. shoes. Leave empty for every size of every child.")] = None,
) -> dict:
    """Every child's current sizes, with how many days old each value is and a stale flag once it's
    older than six months. For "what shoe sizes are the kids" before a shopping trip."""

    def go():
        with get_conn() as conn:
            cat = _resolve_category(conn, category, must_exist=True) if category else None
            items = kids.sizes(conn, category=cat)
        stale = [s for s in items if s["stale"]]
        return {
            "sizes": [_size_brief(s) for s in items],
            "stale_count": len(stale),
            "message": (
                "No sizes recorded yet." if not items
                else f"{len(items)} size(s)." + (f" {len(stale)} are over {kids.STALE_SIZE_DAYS} days old and worth re-checking." if stale else "")
            ),
        }

    return _run(go)


def add_need(
    child: Annotated[str, Field(description="Who needs it: the child's first name.")],
    category: Annotated[str, Field(description="What they need: winter coat, rain boots, swimsuit, backpack...")],
    season: Annotated[str | None, Field(description="Which season or occasion, e.g. 'winter 2026', 'back to school'.")] = None,
    notes: Annotated[str | None, Field(description="Size, color, where to buy, anything useful.")] = None,
    add_to_shopping: Annotated[bool, Field(description="Also put it on the shopping list.")] = False,
    idempotency_key: IdemKey = None,
) -> dict:
    """Note that a child needs something, like "Ava needs a winter coat" or "the boys need rain boots
    before fall". Stays open until someone marks it handled with mark_have. If the same need is already
    open it's returned as a duplicate rather than added twice."""

    def go():
        with get_conn() as conn:
            kid = _resolve_child(conn, child)
            cat = _resolve_category(conn, category)
        res = core.create_need(
            core.NeedIn(child_id=kid["id"], category=cat, season=season or "", notes=notes or "", add_to_shopping=add_to_shopping),
            Response(),
            actor=_ACTOR,
        )
        if res["duplicate"]:
            return {"created": False, "duplicate": True, "need": _need_brief(res), "message": f"{kid['name']} already needs {cat.lower()}; nothing was added."}
        return {
            "created": True,
            "duplicate": False,
            "need": _need_brief(res),
            "message": f"Noted: {kid['name']} needs {cat.lower()}" + (f" for {season}" if season else "") + (". Added to the shopping list too." if add_to_shopping else "."),
        }

    return _idempotent(idempotency_key, "add_need", lambda: _run(go))


def mark_have(
    child: Annotated[str, Field(description="The child's first name.")],
    category: Annotated[str, Field(description="Which need is handled, e.g. winter coat.")],
    idempotency_key: IdemKey = None,
) -> dict:
    """Mark a child's need as handled, like "we got Ava's winter coat". Closes the open need (and
    checks off its shopping-list item if it had one), recording who handled it."""

    def go():
        with get_conn() as conn:
            kid = _resolve_child(conn, child)
            cat = _resolve_category(conn, category, must_exist=True)
            open_ = kids.needs(conn, kid["id"], cat)
            if not open_:
                have = kids.needs(conn, kid["id"], cat, status="have")
                still = [n["category"] for n in kids.needs(conn, kid["id"])]
                raise ToolError(
                    f"{kid['name']} has no open need for {cat.lower()}"
                    + (" (it was already marked handled)" if have else "")
                    + (f". Still open: {', '.join(still)}." if still else ". Nothing is open for them.")
                )
            if len(open_) > 1:
                raise _AmbiguousError(_disambiguation("need", f"{kid['name']}'s {cat.lower()}", [_need_brief(n) for n in open_]))
        fresh = core.update_need(open_[0]["id"], core.NeedPatch(status="have"), actor=_ACTOR)
        return {"handled": True, "need": _need_brief(fresh), "message": f"Marked {kid['name']}'s {cat.lower()} as handled."}

    return _idempotent(idempotency_key, "mark_have", lambda: _run(go))


def list_needs(
    child: Annotated[str | None, Field(description="Only this child. Leave empty for all the kids.")] = None,
    category: Annotated[str | None, Field(description="Only this kind of thing, e.g. shoes.")] = None,
    season: Annotated[str | None, Field(description="Only this season, e.g. 'winter 2026'.")] = None,
    status: Annotated[str | None, Field(description="needed (default), have, or all.")] = None,
) -> dict:
    """What the kids still need, oldest first, with how long each has been open. For "what do the kids
    need" or "is Henry's coat handled" (use status 'all' to see handled ones)."""

    def go():
        want = (status or "needed").strip().lower()
        if want not in (*kids.NEED_STATUSES, "all"):
            raise ToolError(f"status should be needed, have or all, not '{status}'.")
        with get_conn() as conn:
            kid = _resolve_child(conn, child) if child else None
            cat = _resolve_category(conn, category, must_exist=True) if category else None
            items = kids.needs(conn, kid["id"] if kid else None, cat, season, want)
        return {
            "needs": [_need_brief(n) for n in items],
            "message": "Nothing on the list." if not items else f"{len(items)} item(s).",
        }

    return _run(go)


def seasonal_check(
    categories: Annotated[list[str], Field(description="The kinds of things to check, e.g. ['winter coat', 'snow boots', 'gloves'].")],
    season: Annotated[str, Field(description="The season this is for, e.g. 'winter 2026'.")],
) -> dict:
    """Go through every child at once for a season: "do all the kids have coats, boots and gloves for
    winter?" One row per child per category with their current size (and how old it is) and whether
    that need is open, handled, or was never recorded. Then use add_need for the gaps."""

    def go():
        if not categories:
            raise ToolError("Give at least one category to check, e.g. ['winter coat', 'boots'].")
        with get_conn() as conn:
            cats = [_resolve_category(conn, c, prefer_existing=True) for c in categories if c.strip()]
            rows = kids.seasonal_check(conn, cats, season.strip())
        gaps = [r for r in rows if r["status"] != "have"]
        return {
            "season": season,
            "rows": rows,
            "gaps": len(gaps),
            "message": (
                "No children recorded yet." if not rows
                else f"All set for {season}." if not gaps
                else f"{len(gaps)} of {len(rows)} child/category pairs are not marked handled for {season}: "
                + ", ".join(f"{r['child']} {r['category'].lower()} ({r['status']})" for r in gaps) + "."
            ),
        }

    return _run(go)


def list_pantry(
    location: Annotated[str | None, Field(description="Only this shelf/area, e.g. Pantry, Fridge, Freezer, Baking.")] = None,
    low_only: Annotated[bool, Field(description="Only items that are low or below their par level.")] = False,
    expiring_within_days: Annotated[int | None, Field(description="Only items expiring within this many days.")] = None,
    query: Annotated[str | None, Field(description="A word to search for in names, e.g. 'rice'.")] = None,
) -> list[dict]:
    """What's in the pantry, fridge and freezer with quantities, par levels and expiry dates.
    Ask for what's low, what's expiring soon, or search by name."""
    with get_conn() as conn:
        items = _all_pantry(conn)
    if location:
        loc = location.lower()
        items = [p for p in items if similarity(loc, p["category"]) >= 0.72 or loc in p["category"].lower()]
    if low_only:
        items = [p for p in items if p["low"]]
    if expiring_within_days is not None:
        items = [p for p in items if p["days_to_expiry"] is not None and p["days_to_expiry"] <= expiring_within_days]
    if query:
        items = [p for p in items if similarity(query, p["name"]) >= 0.55]
    return [_pantry_brief(p) for p in items]


class PantryItemSpec(core.BaseModel):
    name: str = core.Field(min_length=1, max_length=120, description="e.g. 'Jasmine rice'")
    quantity: str = core.Field(default="", max_length=60, description="Free text like '2 bags' or '1'")
    location: str = core.Field(default="", max_length=60, description="Pantry, Fridge, Freezer, ...")
    par_level: float | None = core.Field(default=None, ge=0, description="Reorder when quantity is at or below this")
    expires_on: str | None = core.Field(default=None, description="YYYY-MM-DD")


def add_pantry_items(
    items: Annotated[list[PantryItemSpec], Field(description="One entry per item; fifteen at once is fine.")],
    idempotency_key: IdemKey = None,
) -> dict:
    """Put groceries into the pantry list, like "we bought eggs, two bags of jasmine rice and
    olive oil". Anything that's already in the pantry under a similar name is returned flagged
    `duplicate` and not added twice (update it instead)."""

    def go():
        if not items:
            raise ToolError("Nothing to add: the items list is empty.")
        bodies = [
            core.PantryItemIn(
                name=s.name,
                quantity=s.quantity,
                category=s.location,
                par_level=s.par_level,
                expires_on=_parse_date(s.expires_on, f"expires_on for {s.name}"),
                updated_by=DEFAULT_ACTOR,
            )
            for s in items
        ]
        rows = core.create_pantry_item(bodies)
        added = [r for r in rows if not r["duplicate"]]
        dupes = [r for r in rows if r["duplicate"]]
        return {
            "added": [_pantry_brief(r) for r in added],
            "duplicates": [{**_pantry_brief(r), "duplicate": True, "requested_name": r["requested_name"]} for r in dupes],
            "message": f"Added {len(added)}."
            + (f" Already had {len(dupes)}: " + ", ".join(f"'{d['requested_name']}' is '{d['name']}'" for d in dupes) if dupes else ""),
        }

    return _idempotent(idempotency_key, "add_pantry_items", lambda: _run(go))


def update_pantry_item(
    item: Id,
    quantity: Annotated[str | None, Field(description="New quantity, free text: '1', 'half a bag', '3 cans'.")] = None,
    quantity_delta: Annotated[float | None, Field(description="Add (or with a minus sign, subtract) this much from the numeric quantity.")] = None,
    location: Annotated[str | None, Field(description="Move it to this shelf/area.")] = None,
    expires_on: Annotated[str | None, Field(description="New expiry date YYYY-MM-DD, or 'none' to clear.")] = None,
    par_level: Annotated[float | None, Field(description="Reorder point; quantity at or below this counts as low. -1 clears it.")] = None,
    idempotency_key: IdemKey = None,
) -> dict:
    """Change one pantry item, like "we're down to half a bag of jasmine rice", "used two eggs",
    or "set the par level for coffee to 1". If the name could mean more than one item this
    returns needs_disambiguation and changes nothing."""

    def go():
        with get_conn() as conn:
            p = _resolve_pantry(conn, item)
        fields: dict = {}
        if quantity is not None:
            fields["quantity"] = quantity
        if quantity_delta is not None:
            current = core.quantity_number(p["quantity"])
            if current is None and p["quantity"].strip():
                raise ToolError(
                    f"'{p['name']}' has a quantity of '{p['quantity']}', which isn't a number, so I can't add "
                    f"{quantity_delta} to it. Set the quantity instead."
                )
            new = max(0.0, (current or 0.0) + quantity_delta)
            suffix = core._NUMBER_RE.sub("", p["quantity"], count=1).strip()
            fields["quantity"] = (f"{new:g} {suffix}").strip()
        if location is not None:
            fields["category"] = location
        if expires_on is not None:
            fields["expires_on"] = None if expires_on.strip().lower() in ("none", "clear", "") else _parse_date(expires_on, "expires_on")
        if par_level is not None:
            fields["par_level"] = None if par_level < 0 else par_level
        if not fields:
            raise ToolError("Nothing to change: give a quantity, quantity_delta, location, expires_on or par_level.")
        patch = core.PantryItemPatch(**fields, updated_by=DEFAULT_ACTOR)
        updated = core.update_pantry_item(p["id"], patch)
        return {"updated": True, "item": _pantry_brief(updated), "was": _pantry_brief(p)}

    return _idempotent(idempotency_key, "update_pantry_item", lambda: _run(go))


def remove_pantry_item(item: Id, idempotency_key: IdemKey = None) -> dict:
    """Take something off the pantry list entirely, like "we don't keep almond milk anymore".
    To say you ran out, update the quantity to 0 instead."""

    def go():
        with get_conn() as conn:
            p = _resolve_pantry(conn, item)
        core.delete_pantry_item(p["id"])
        return {"removed": True, "item": _pantry_brief(p)}

    return _idempotent(idempotency_key, "remove_pantry_item", lambda: _run(go))


def _shopping_brief(s: dict) -> dict:
    return {
        "id": s["id"],
        "item": s["name"],
        "notes": s["notes"],
        "assignee": s["assignee"],
        "due_on": s["due_on"],
        "overdue": s["overdue"],
        "done": s["done"],
        "added_by": s["added_by"],
        "created_at": s["created_at"],
    }


def list_shopping() -> dict:
    """The shopping list: things people were asked to buy (with who it's assigned to), things
    flagged as needed for a task, plus pantry items that are low or below par."""
    notes = core.shopping_list()
    items = core.list_shopping_items()
    with get_conn() as conn:
        low = [_pantry_brief(p) for p in _all_pantry(conn) if p["low"]]
    return {
        "items": [_shopping_brief(s) for s in items],
        "to_buy_for_tasks": [
            {"note_id": n["id"], "item": n["body"], "task": n["task_title"], "room": n["room_name"], "added_by": n["author"]}
            for n in notes
        ],
        "pantry_low": low,
    }


def add_to_shopping(
    items: Annotated[list[str], Field(description="Names of things to buy, e.g. ['dish soap', 'jasmine rice'].")],
    idempotency_key: IdemKey = None,
) -> dict:
    """Put things on the shopping list, like "we need dish soap and paper towels". An item already
    in the pantry is marked low; anything new is added to the pantry as low so it shows on To Buy."""

    def go():
        if not items:
            raise ToolError("Nothing to add: the items list is empty.")
        marked, added = [], []
        with get_conn() as conn:
            for name in items:
                existing = core.find_pantry_duplicate(conn, name)
                if existing:
                    conn.execute(
                        "UPDATE pantry_items SET low = 1, updated_by = ?, updated_at = ? WHERE id = ?",
                        (DEFAULT_ACTOR, _now_iso(), existing["id"]),
                    )
                    marked.append(existing["name"])
                else:
                    core._insert_pantry_item(conn, core.PantryItemIn(name=name, low=True, updated_by=DEFAULT_ACTOR))
                    added.append(name.strip())
        return {"marked_low": marked, "added": added, "message": f"On the list: {', '.join(marked + added)}."}

    return _idempotent(idempotency_key, "add_to_shopping", lambda: _run(go))


_BUY_WORDS = re.compile(
    r"\b(buy|get|grab|pick up|purchase|order|shop for|restock|more|groceries|from (the )?(store|pharmacy|costco|target|walmart))\b"
)


def _infer_kind(item: str, room: str | None) -> str:
    if room:
        return "task"
    return "shopping" if _BUY_WORDS.search(item.lower()) else "task"


def add_to_list(
    person: Annotated[str, Field(description="Whose list: a first name like 'Susan' or 'Magnus'.")],
    item: Annotated[str, Field(description="The thing to do or buy, e.g. 'Return the library books' or 'Buy dish soap'.")],
    kind: Annotated[str | None, Field(description="'task' (something to do) or 'shopping' (something to buy). Leave empty and it's inferred from the wording.")] = None,
    room: Annotated[str | None, Field(description="For a task: the room it's about, if any (name or id).")] = None,
    due_on: Annotated[str | None, Field(description="When it's needed by, YYYY-MM-DD or today/tomorrow.")] = None,
    notes: Annotated[str | None, Field(description="Any extra detail.")] = None,
    idempotency_key: IdemKey = None,
) -> dict:
    """Put something on a specific person's list, like "add 'pick up the dry cleaning' to Susan's
    list" or "Vanessa needs to buy diapers". A thing to buy goes on their shopping list, a thing
    to do becomes a task; they get a notification. If the name isn't one of the household's
    people, this returns who they are instead of guessing."""

    def go():
        k = (kind or "").strip().lower() or _infer_kind(item, room)
        if k not in ("task", "shopping"):
            raise ToolError(f"kind should be 'task' or 'shopping', not '{kind}'.")
        with get_conn() as conn:
            p = _resolve_person(conn, person)
            if k == "task":
                task = _create_task(conn, item, room, due_on, None, notes, p["name"])
                return {
                    "created": True,
                    "kind": "task",
                    "assigned_to": p["name"],
                    "task": _task_brief(task),
                    "message": f"Added the task '{task['title']}' to {p['name']}'s list and notified them.",
                }
            s = core.create_shopping_item(
                core.ShoppingItemIn(name=item, notes=notes or "", assignee_id=p["id"], due_on=_parse_date(due_on, "due_on")),
                actor=_ACTOR,
            )
            return {
                "created": True,
                "kind": "shopping",
                "assigned_to": p["name"],
                "shopping_item": _shopping_brief(s),
                "message": f"Added '{s['name']}' to {p['name']}'s shopping list and notified them.",
            }

    return _idempotent(idempotency_key, "add_to_list", lambda: _run(go))


def list_for_person(
    person: Annotated[str, Field(description="Whose list: a first name like 'Susan'.")],
    include_done: Annotated[bool, Field(description="Also show things already finished or bought.")] = False,
) -> dict:
    """What's on one person's list - their assigned tasks and things to buy together, oldest
    first, with anything overdue flagged. Like "what does Vanessa have to do?"."""

    def go():
        with get_conn() as conn:
            p = _resolve_person(conn, person)
            user = core.auth.get_user(conn, p["id"])
            lst = core.person_list(conn, user, include_done)
        n = len(lst["items"])
        return {
            "person": p["name"],
            "items": lst["items"],
            "overdue_count": lst["overdue_count"],
            "message": f"{p['name']} has {n} thing{'s' if n != 1 else ''} on their list"
            + (f", {lst['overdue_count']} overdue." if lst["overdue_count"] else "."),
        }

    return _run(go)


def find(query: Annotated[str, Field(description="A word or two, e.g. 'filter', 'rice', 'bathroom'.")]) -> dict:
    """Search everything at once - rooms, tasks, upkeep jobs, pantry items and the shopping list -
    for a name. Use it when you're not sure what something is called or where it lives."""
    with get_conn() as conn:
        rooms = [_room_brief(r) for r in _all_rooms(conn) if similarity(query, r["name"]) >= 0.55]
        tasks = [
            _task_brief(t)
            for t in _all_tasks(conn)
            if similarity(query, t["title"]) >= 0.55 or similarity(query, f"{t['title']} {_task_room_label(t)}") >= 0.55
        ]
        upkeep = [i for i in _all_upkeep(conn) if similarity(query, i["name"]) >= 0.55 or similarity(query, i["category"]) >= 0.72]
        pantry = [_pantry_brief(p) for p in _all_pantry(conn) if similarity(query, p["name"]) >= 0.55]
    shopping = [n for n in core.shopping_list() if similarity(query, n["body"]) >= 0.55]
    shopping_items = [_shopping_brief(s) for s in core.list_shopping_items() if similarity(query, s["name"]) >= 0.55]
    total = len(rooms) + len(tasks) + len(upkeep) + len(pantry) + len(shopping) + len(shopping_items)
    return {
        "query": query,
        "rooms": rooms,
        "tasks": tasks,
        "upkeep": upkeep,
        "pantry": pantry,
        "shopping": [{"item": n["body"], "task": n["task_title"], "room": n["room_name"]} for n in shopping] + shopping_items,
        "message": f"Found {total} thing{'s' if total != 1 else ''} matching '{query}'." if total else f"Nothing matches '{query}'.",
    }
