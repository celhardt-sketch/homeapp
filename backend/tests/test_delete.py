"""Deleting things once they're done or used up: prescriptions, children, notes, needs, and the
MCP `remove` tool that covers tasks, shopping items, upkeep, declutter spots, prescriptions and children."""

import pytest
from fastmcp.exceptions import ToolError

from app.db import get_conn
from tests.test_mcp import mcp, server_url  # noqa: F401  (fixtures)


@pytest.fixture
def child(client, admin_headers):
    c = client.post("/api/children", json={"name": "Quentin"}, headers=admin_headers).json()
    yield c
    with get_conn() as conn:
        conn.execute("DELETE FROM children WHERE id = ?", (c["id"],))


def test_delete_prescription_removes_pickups_helper_refused(client, admin_headers, susan_headers, child):
    rx = client.post("/api/prescriptions", json={"child_id": child["id"], "name": "Ritalin"}, headers=admin_headers).json()
    client.post(f"/api/prescriptions/{rx['id']}/pickups", json={"picked_up_on": "2026-01-01"}, headers=susan_headers)
    assert client.delete(f"/api/prescriptions/{rx['id']}", headers=susan_headers).status_code == 403
    assert client.delete(f"/api/prescriptions/{rx['id']}", headers=admin_headers).status_code == 204
    assert client.get(f"/api/prescriptions/{rx['id']}/pickups", headers=admin_headers).status_code == 404
    with get_conn() as conn:
        assert conn.execute("SELECT COUNT(*) FROM pickups WHERE prescription_id = ?", (rx["id"],)).fetchone()[0] == 0
    assert client.delete(f"/api/prescriptions/{rx['id']}", headers=admin_headers).status_code == 404


def test_delete_child_refused_while_prescriptions_remain(client, admin_headers, child):
    rx = client.post("/api/prescriptions", json={"child_id": child["id"], "name": "Zyrtec"}, headers=admin_headers).json()
    r = client.delete(f"/api/children/{child['id']}", headers=admin_headers)
    assert r.status_code == 409
    assert "Quentin still has 1 prescription" in r.json()["detail"]
    client.delete(f"/api/prescriptions/{rx['id']}", headers=admin_headers)
    assert client.delete(f"/api/children/{child['id']}", headers=admin_headers).status_code == 204
    assert client.get(f"/api/children/{child['id']}", headers=admin_headers).status_code == 404


def test_delete_note_and_need(client, susan_headers, admin_headers, child):
    task = client.post("/api/tasks", json={"title": "Scrub sink"}, headers=susan_headers).json()
    note = client.post(f"/api/tasks/{task['id']}/notes", json={"author": "Susan", "body": "buy sponges", "needs_purchase": True}, headers=susan_headers).json()
    assert client.delete(f"/api/notes/{note['id']}", headers=susan_headers).status_code == 204
    assert note["id"] not in {n["id"] for n in client.get("/api/shopping", headers=susan_headers).json()}
    assert client.delete(f"/api/notes/{note['id']}", headers=susan_headers).status_code == 404

    need = client.post("/api/needs", json={"child_id": child["id"], "category": "Boots"}, headers=susan_headers).json()
    assert client.delete(f"/api/needs/{need['id']}", headers=susan_headers).status_code == 204
    assert client.get(f"/api/needs?child_id={child['id']}", headers=susan_headers).json() == []
    client.delete(f"/api/tasks/{task['id']}", headers=admin_headers)


def test_mcp_remove_each_kind(mcp, client, admin_headers, child):
    s = client.post("/api/shopping-items", json={"name": "Dish soap refill pods"}, headers=admin_headers).json()
    r = mcp("remove", kind="shopping", item="dish soap refil")
    assert r["removed"] and r["item"]["id"] == s["id"]
    assert client.get("/api/shopping-items?include_done=true", headers=admin_headers).status_code == 200
    assert s["id"] not in {x["id"] for x in client.get("/api/shopping-items?include_done=true", headers=admin_headers).json()}

    t = client.post("/api/tasks", json={"title": "Fix the squeaky gate"}, headers=admin_headers).json()
    assert mcp("remove", kind="tasks", item="squeaky gate")["item"]["id"] == t["id"]

    d = client.post("/api/declutter", json={"name": "Attic boxes"}, headers=admin_headers).json()
    assert mcp("remove", kind="declutter spot", item="attic")["item"]["id"] == d["id"]

    u = client.post("/api/upkeep", json={"name": "Descale kettle", "interval_days": 60}, headers=admin_headers).json()
    assert mcp("remove", kind="upkeep", item="descale kettle")["item"]["id"] == u["id"]

    rx = client.post("/api/prescriptions", json={"child_id": child["id"], "name": "Flovent"}, headers=admin_headers).json()
    with pytest.raises(ToolError, match="whose prescription"):
        mcp("remove", kind="prescription", item="flovent")
    with pytest.raises(ToolError, match="still has 1 prescription"):
        mcp("remove", kind="child", item="quentin")
    assert mcp("remove", kind="prescription", item="flovent", child="quentin")["item"]["id"] == rx["id"]
    assert mcp("remove", kind="child", item="quentin")["item"]["id"] == child["id"]

    with pytest.raises(ToolError, match="I can remove one of"):
        mcp("remove", kind="room", item="kitchen")


def test_mcp_remove_is_idempotent_and_never_guesses(mcp, client, admin_headers):
    a = client.post("/api/shopping-items", json={"name": "Jasmine rice"}, headers=admin_headers).json()
    b = client.post("/api/shopping-items", json={"name": "Brown rice"}, headers=admin_headers).json()
    r = mcp("remove", kind="shopping", item="rice")
    assert r.get("needs_disambiguation")
    ids = {x["id"] for x in client.get("/api/shopping-items", headers=admin_headers).json()}
    assert {a["id"], b["id"]} <= ids

    first = mcp("remove", kind="shopping", item="jasmine rice", idempotency_key="rm-1")
    again = mcp("remove", kind="shopping", item="jasmine rice", idempotency_key="rm-1")
    assert again.pop("replayed") is True
    assert first == again and first["item"]["id"] == a["id"]
    client.delete(f"/api/shopping-items/{b['id']}", headers=admin_headers)


def test_helper_can_add_and_delete_on_every_list(client, susan_headers, vanessa_headers):
    """Upkeep, declutter, to-buy and pantry are shared lists: any signed-in person can add or delete."""
    cases = [
        ("/api/upkeep", {"name": "Wipe fridge coils", "interval_days": 180}, "/api/upkeep"),
        ("/api/declutter", {"name": "Hall bench basket"}, "/api/declutter"),
        ("/api/shopping-items", {"name": "Lightbulbs"}, "/api/shopping-items"),
        ("/api/pantry", {"name": "Dried mango", "quantity": "1"}, "/api/pantry"),
    ]
    for post_path, body, delete_prefix in cases:
        r = client.post(post_path, json=body, headers=susan_headers)
        assert r.status_code == 201, (post_path, r.text)
        created = r.json()
        item_id = created["id"] if isinstance(created, dict) else created[0]["id"]
        assert client.delete(f"{delete_prefix}/{item_id}", headers=vanessa_headers).status_code == 204, post_path
