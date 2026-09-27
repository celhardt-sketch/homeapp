"""Prescription refill tracking: children, prescriptions, pickups.

refill_status is derived from the latest pickup, never stored:
  days_since_pickup <  refill_after_days -> ok
  days_since_pickup >= refill_after_days -> refill_due
  days_since_pickup >= days_supply       -> urgent (the supply is gone)
A user can mark a prescription "called" which shows as called_waiting instead of refill_due for
CALLED_WAITING_DAYS; it never hides urgent. Logging a pickup clears it.
"""

import sqlite3
from datetime import date, datetime, timezone

from .db import get_conn

CALLED_WAITING_DAYS = 2
DUE_STATUSES = ("refill_due", "urgent")
DETAIL_SETTING = "refill_notify_detail"  # '1' -> notification bodies may name the medication
ALWAYS_NOTIFY = "Courtney"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def latest_pickup(conn: sqlite3.Connection, prescription_id: int) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM pickups WHERE prescription_id = ? ORDER BY picked_up_on DESC, id DESC LIMIT 1",
        (prescription_id,),
    ).fetchone()


def called_waiting_active(p: sqlite3.Row | dict, today: date) -> bool:
    if not p["called_on"]:
        return False
    return (today - date.fromisoformat(p["called_on"])).days < CALLED_WAITING_DAYS


def serialize_prescription(conn: sqlite3.Connection, p: sqlite3.Row, today: date | None = None) -> dict:
    today = today or date.today()
    child = conn.execute("SELECT name FROM children WHERE id = ?", (p["child_id"],)).fetchone()
    last = latest_pickup(conn, p["id"])
    assignee = conn.execute("SELECT name FROM users WHERE id = ?", (p["assignee_id"],)).fetchone() if p["assignee_id"] else None
    days_since = None
    supply_left = None
    status = "no_pickup"
    if last:
        days_since = (today - date.fromisoformat(last["picked_up_on"])).days
        supply_left = p["days_supply"] - days_since
        if days_since >= p["days_supply"]:
            status = "urgent"
        elif days_since >= p["refill_after_days"]:
            status = "called_waiting" if called_waiting_active(p, today) else "refill_due"
        else:
            status = "ok"
    return {
        "id": p["id"],
        "child_id": p["child_id"],
        "child": child["name"] if child else None,
        "name": p["name"],
        "pharmacy": p["pharmacy"],
        "contact_name": p["contact_name"],
        "contact_phone": p["contact_phone"],
        "days_supply": p["days_supply"],
        "refill_after_days": p["refill_after_days"],
        "active": bool(p["active"]),
        "notes": p["notes"],
        "assignee_id": p["assignee_id"],
        "assignee": assignee["name"] if assignee else None,
        "last_picked_up_on": last["picked_up_on"] if last else None,
        "last_picked_up_by": last["picked_up_by"] if last else None,
        "days_since_pickup": days_since,
        "days_of_supply_left": supply_left,
        "refill_status": status,
        "called_on": p["called_on"] if called_waiting_active(p, today) else None,
        "called_by": p["called_by"] if called_waiting_active(p, today) else "",
        "called_notes": p["called_notes"] if called_waiting_active(p, today) else "",
    }


def active_prescriptions(conn: sqlite3.Connection, today: date | None = None) -> list[dict]:
    rows = conn.execute(
        "SELECT p.* FROM prescriptions p JOIN children c ON c.id = p.child_id "
        "WHERE p.active = 1 AND c.active = 1 ORDER BY c.name, p.name"
    ).fetchall()
    return [serialize_prescription(conn, p, today) for p in rows]


def due_refills(conn: sqlite3.Connection, today: date | None = None) -> list[dict]:
    """Everything refill_due or urgent, most urgent first. Stays here until a pickup is logged."""
    due = [p for p in active_prescriptions(conn, today) if p["refill_status"] in DUE_STATUSES]
    return sorted(due, key=lambda p: (p["days_of_supply_left"], p["child"], p["name"]))


def status_section(conn: sqlite3.Connection) -> list[dict]:
    """The refills digest for /api/status: only what's needed to act on the day-28 window."""
    return [
        {
            "prescription_id": p["id"],
            "child": p["child"],
            "prescription": p["name"],
            "refill_status": p["refill_status"],
            "days_of_supply_left": p["days_of_supply_left"],
        }
        for p in due_refills(conn)
    ]


def detail_enabled(conn: sqlite3.Connection) -> bool:
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (DETAIL_SETTING,)).fetchone()
    return bool(row and row["value"] == "1")


def set_detail_enabled(conn: sqlite3.Connection, on: bool) -> None:
    conn.execute(
        "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (DETAIL_SETTING, "1" if on else "0"),
    )


def notification_message(p: dict, detail: bool) -> str:
    what = f"{p['name']} for {p['child']}" if detail else p["child"]
    if p["refill_status"] == "urgent":
        return f"URGENT: {what} is out of supply ({abs(p['days_of_supply_left'])} day(s) past), refill today, open the app"
    return f"Refill due for {what}, open the app"


def recipients(conn: sqlite3.Connection, p: dict) -> list[int]:
    """Courtney always (every admin if there is no Courtney), plus whoever the prescription is assigned to."""
    ids = [
        r["id"]
        for r in conn.execute("SELECT id FROM users WHERE active = 1 AND lower(name) = lower(?)", (ALWAYS_NOTIFY,))
    ]
    if not ids:
        ids = [r["id"] for r in conn.execute("SELECT id FROM users WHERE active = 1 AND role = 'admin'")]
    if p["assignee_id"] and p["assignee_id"] not in ids:
        ids.append(p["assignee_id"])
    return ids


def create_due_notifications(today: date | None = None) -> int:
    """One in-app notification per person per due prescription per day, for as long as it stays due.
    Rides on the notifications table, so the reminder loop emails them like assignments."""
    today = today or date.today()
    created = 0
    with get_conn() as conn:
        detail = detail_enabled(conn)
        for p in due_refills(conn, today):
            msg = notification_message(p, detail)
            for uid in recipients(conn, p):
                already = conn.execute(
                    "SELECT 1 FROM notifications WHERE kind = 'refill' AND ref_id = ? AND user_id = ? "
                    "AND substr(created_at, 1, 10) = ?",
                    (p["id"], uid, today.isoformat()),
                ).fetchone()
                if already:
                    continue
                conn.execute(
                    "INSERT INTO notifications (user_id, kind, ref_id, message, created_by, created_at) "
                    "VALUES (?, 'refill', ?, ?, 'Refill reminder', ?)",
                    (uid, p["id"], msg, _now_iso()),
                )
                created += 1
    return created
