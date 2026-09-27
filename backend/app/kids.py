"""Children's clothing sizes and things they need.

Sizes: one current value per (child, category), e.g. Henry / Shoes / "13 toddler". Kids grow, so
every read says how old the value is and flags it stale after STALE_SIZE_DAYS.

Needs: "Ava needs a winter coat" -> an open need until someone marks it 'have'. A need can be
pushed onto the shopping list. Categories are free text but folded together fuzzily so "shoe",
"Shoes" and "shoes" are one category.
"""

import sqlite3
from datetime import date, datetime, timezone

from difflib import SequenceMatcher

from .matching import normalize

STALE_SIZE_DAYS = 180
NEED_STATUSES = ("needed", "have")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _age_days(iso: str, today: date) -> int:
    return (today - date.fromisoformat(iso[:10])).days


def known_categories(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        "SELECT category FROM child_sizes UNION SELECT category FROM child_needs ORDER BY 1"
    ).fetchall()
    return [r[0] for r in rows]


def canonical_category(conn: sqlite3.Connection, text: str) -> str:
    """Reuse an existing category label when the new one is the same word give or take plural or a
    typo ("shoe" / "Shoes"). "pants" does not fold into "Snow Pants"; that's a different thing."""
    text = " ".join(text.split())
    q = normalize(text)
    for existing in known_categories(conn):
        if q == normalize(existing) or SequenceMatcher(None, q, normalize(existing)).ratio() >= 0.85:
            return existing
    return text[:1].upper() + text[1:] if text else text


def serialize_size(s: sqlite3.Row, today: date | None = None) -> dict:
    today = today or date.today()
    age = _age_days(s["updated_at"], today)
    return {
        "id": s["id"],
        "child_id": s["child_id"],
        "child": s["child"],
        "category": s["category"],
        "value": s["value"],
        "notes": s["notes"],
        "updated_at": s["updated_at"],
        "updated_by": s["updated_by"],
        "age_days": age,
        "stale": age >= STALE_SIZE_DAYS,
    }


def sizes(conn: sqlite3.Connection, child_id: int | None = None, category: str | None = None) -> list[dict]:
    where, params = ["c.active = 1"], []
    if child_id is not None:
        where.append("s.child_id = ?")
        params.append(child_id)
    if category:
        where.append("s.category = ?")
        params.append(category)
    rows = conn.execute(
        f"SELECT s.*, c.name AS child FROM child_sizes s JOIN children c ON c.id = s.child_id "
        f"WHERE {' AND '.join(where)} ORDER BY c.name, s.category",
        params,
    ).fetchall()
    return [serialize_size(r) for r in rows]


def set_size(conn: sqlite3.Connection, child_id: int, category: str, value: str, notes: str, actor_name: str, user_id: int | None) -> dict:
    conn.execute(
        """
        INSERT INTO child_sizes (child_id, category, value, notes, updated_at, updated_by, user_id)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(child_id, category) DO UPDATE SET
            value = excluded.value, notes = excluded.notes, updated_at = excluded.updated_at,
            updated_by = excluded.updated_by, user_id = excluded.user_id
        """,
        (child_id, category, value.strip(), notes.strip(), now_iso(), actor_name, user_id),
    )
    return sizes(conn, child_id, category)[0]


def serialize_need(n: sqlite3.Row, today: date | None = None) -> dict:
    today = today or date.today()
    return {
        "id": n["id"],
        "child_id": n["child_id"],
        "child": n["child"],
        "category": n["category"],
        "season": n["season"],
        "notes": n["notes"],
        "status": n["status"],
        "shopping_item_id": n["shopping_item_id"],
        "created_at": n["created_at"],
        "created_by": n["created_by"],
        "open_for_days": _age_days(n["created_at"], today) if n["status"] == "needed" else None,
        "resolved_at": n["resolved_at"],
        "resolved_by": n["resolved_by"],
    }


def needs(
    conn: sqlite3.Connection,
    child_id: int | None = None,
    category: str | None = None,
    season: str | None = None,
    status: str | None = "needed",
) -> list[dict]:
    where, params = ["c.active = 1"], []
    if child_id is not None:
        where.append("n.child_id = ?")
        params.append(child_id)
    if category:
        where.append("n.category = ?")
        params.append(category)
    if season:
        where.append("lower(n.season) = lower(?)")
        params.append(season)
    if status and status != "all":
        where.append("n.status = ?")
        params.append(status)
    rows = conn.execute(
        f"SELECT n.*, c.name AS child FROM child_needs n JOIN children c ON c.id = n.child_id "
        f"WHERE {' AND '.join(where)} ORDER BY n.created_at, n.id",
        params,
    ).fetchall()
    return [serialize_need(r) for r in rows]


def fetch_need(conn: sqlite3.Connection, need_id: int) -> dict | None:
    r = conn.execute(
        "SELECT n.*, c.name AS child FROM child_needs n JOIN children c ON c.id = n.child_id WHERE n.id = ?",
        (need_id,),
    ).fetchone()
    return serialize_need(r) if r else None


def open_need(conn: sqlite3.Connection, child_id: int, category: str, season: str) -> dict | None:
    """The existing open need for the same child, category and season, if any (the duplicate)."""
    for n in needs(conn, child_id, category, status="needed"):
        if n["season"].lower() == season.lower():
            return n
    return None


def seasonal_check(conn: sqlite3.Connection, categories: list[str], season: str) -> list[dict]:
    """One row per child per category: current size (with age), and whether a need for this season
    is open, already handled, or was never recorded."""
    kids = conn.execute("SELECT id, name FROM children WHERE active = 1 ORDER BY name").fetchall()
    out = []
    for kid in kids:
        for cat in categories:
            size = next(iter(sizes(conn, kid["id"], cat)), None)
            recorded = [n for n in needs(conn, kid["id"], cat, season, status="all")]
            open_ = [n for n in recorded if n["status"] == "needed"]
            out.append(
                {
                    "child_id": kid["id"],
                    "child": kid["name"],
                    "category": cat,
                    "season": season,
                    "size": size["value"] if size else None,
                    "size_age_days": size["age_days"] if size else None,
                    "size_stale": size["stale"] if size else None,
                    "status": "needed" if open_ else ("have" if recorded else "unknown"),
                    "need_id": open_[0]["id"] if open_ else None,
                }
            )
    return out
