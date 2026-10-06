"""Due things land on the admins' lists automatically and can be handed to a helper: upkeep that is
due, refills that are due, due room tasks and open declutter spots. Handing off moves the item to
that person's list, notifies them once, and takes it off the admins' automatic list."""

from datetime import date

import pytest
from fastmcp.exceptions import ToolError

from app.db import get_conn
from tests.conftest import user_id
from tests.test_mcp import _unwrap, mcp, server_url  # noqa: F401  (fixtures)
from tests.test_refills import child, make_rx  # noqa: F401  (fixture)


def _items(client, headers, kind=None, **q):
    lst = client.get("/api/me/list", headers=headers).json()["items"]
    return [i for i in lst if (kind is None or i["kind"] == kind) and all(i[k] == v for k, v in q.items())]


def _notes(client, headers):
    return client.get("/api/me/notifications", headers=headers).json()


def test_due_upkeep_appears_on_admin_lists_only_until_handed_off(client, admin_headers, susan_headers, vanessa_headers):
    up = client.post("/api/upkeep", json={"name": "Handoff furnace filter", "interval_days": 90}, headers=admin_headers).json()
    assert up["status"] == "due" and up["assignee"] is None
    mine = _items(client, admin_headers, "upkeep", id=up["id"])
    assert len(mine) == 1 and mine[0]["auto"] is True and mine[0]["title"] == "Handoff furnace filter"
    assert _items(client, susan_headers, "upkeep", id=up["id"]) == []

    susan = user_id(client, admin_headers, "Susan")
    before = len(_notes(client, susan_headers))
    r = client.post("/api/list/handoff", json={"items": [{"kind": "upkeep", "id": up["id"]}], "assignee_id": susan}, headers=admin_headers)
    assert r.status_code == 200, r.text
    assert r.json()["assignee"] == "Susan" and r.json()["items"][0]["auto"] is False
    assert _items(client, admin_headers, "upkeep", id=up["id"]) == []
    got = _items(client, susan_headers, "upkeep", id=up["id"])
    assert len(got) == 1 and got[0]["auto"] is False
    notes = _notes(client, susan_headers)
    assert len(notes) == before + 1 and "furnace filter" in notes[0]["message"] and notes[0]["created_by"] == "Courtney"
    assert _items(client, vanessa_headers, "upkeep", id=up["id"]) == []

    # Susan logs it: it leaves her list, the handoff is spent, and it is not due again for 90 days
    client.post(f"/api/upkeep/{up['id']}/logs", json={"done_on": date.today().isoformat()}, headers=susan_headers)
    assert _items(client, susan_headers, "upkeep", id=up["id"]) == []
    assert _items(client, admin_headers, "upkeep", id=up["id"]) == []
    item = next(i for i in client.get("/api/upkeep", headers=admin_headers).json() if i["id"] == up["id"])
    assert item["assignee"] is None and item["last_done_by"] == "Susan"
    client.delete(f"/api/upkeep/{up['id']}", headers=admin_headers)


def test_due_refill_appears_for_admins_and_hands_off_without_med_name(client, admin_headers, susan_headers, child):  # noqa: F811
    rx = make_rx(client, admin_headers, child, name="Handoff-secret-med", pickup_days_ago=29)
    mine = _items(client, admin_headers, "refill", id=rx["id"])
    assert len(mine) == 1 and mine[0]["auto"] is True and "Henry" in mine[0]["title"]
    assert "supply left" in mine[0]["detail"]
    assert _items(client, susan_headers, "refill", id=rx["id"]) == []
    ok_rx = make_rx(client, admin_headers, child, name="Not due yet", pickup_days_ago=3)
    assert _items(client, admin_headers, "refill", id=ok_rx["id"]) == []

    susan = user_id(client, admin_headers, "Susan")
    before = len(_notes(client, susan_headers))
    r = client.post("/api/list/handoff", json={"items": [{"kind": "refill", "id": rx["id"]}], "assignee_id": susan}, headers=admin_headers)
    assert r.status_code == 200, r.text
    assert _items(client, admin_headers, "refill", id=rx["id"]) == []
    assert len(_items(client, susan_headers, "refill", id=rx["id"])) == 1
    new = _notes(client, susan_headers)[: len(_notes(client, susan_headers)) - before]
    assert len(new) == 1 and "Handoff-secret-med" not in new[0]["message"] and "Henry" in new[0]["message"]

    # a pickup clears it from Susan's list
    client.post(f"/api/prescriptions/{rx['id']}/pickups", json={"picked_up_on": date.today().isoformat()}, headers=susan_headers)
    assert _items(client, susan_headers, "refill", id=rx["id"]) == []


def test_due_room_task_and_open_declutter_spot_are_automatic(client, admin_headers, vanessa_headers):
    room = client.get("/api/rooms", headers=admin_headers).json()[0]
    task = client.post("/api/tasks", json={"room_id": room["id"], "title": "Handoff wipe baseboards", "frequency_days": 7}, headers=admin_headers).json()
    assert task["status"] == "due"
    auto = _items(client, admin_headers, "task", id=task["id"])
    assert len(auto) == 1 and auto[0]["auto"] is True and auto[0]["room_name"] == room["name"]
    spot = client.post("/api/declutter", json={"name": "Handoff hall closet"}, headers=admin_headers).json()
    spot = spot["created"][0] if "created" in spot else spot
    auto = _items(client, admin_headers, "declutter", id=spot["id"])
    assert len(auto) == 1 and auto[0]["auto"] is True

    vanessa = user_id(client, admin_headers, "Vanessa")
    r = client.post(
        "/api/list/handoff",
        json={"items": [{"kind": "task", "id": task["id"]}, {"kind": "declutter", "id": spot["id"]}], "assignee_id": vanessa},
        headers=admin_headers,
    )
    assert r.status_code == 200, r.text
    assert _items(client, admin_headers, id=task["id"], kind="task") == []
    assert _items(client, admin_headers, id=spot["id"], kind="declutter") == []
    assert len(_items(client, vanessa_headers, "task", id=task["id"])) == 1
    assert len(_items(client, vanessa_headers, "declutter", id=spot["id"])) == 1

    # finishing the spot takes it off her list
    client.patch(f"/api/declutter/{spot['id']}", json={"done": True}, headers=vanessa_headers)
    assert _items(client, vanessa_headers, "declutter", id=spot["id"]) == []
    # the room task stays hers (it recurs) and still records who did it
    client.post(f"/api/tasks/{task['id']}/complete", json={}, headers=vanessa_headers)
    t = _items(client, vanessa_headers, "task", id=task["id"])
    assert len(t) == 1 and t[0]["last_completed_by"] == "Vanessa"
    client.delete(f"/api/tasks/{task['id']}", headers=admin_headers)
    client.delete(f"/api/declutter/{spot['id']}", headers=admin_headers)


def test_helper_can_take_for_self_but_not_hand_to_others(client, admin_headers, susan_headers, vanessa_headers):
    up = client.post("/api/upkeep", json={"name": "Handoff gutters", "interval_days": 180}, headers=admin_headers).json()
    susan = user_id(client, admin_headers, "Susan")
    vanessa = user_id(client, admin_headers, "Vanessa")
    r = client.post("/api/list/handoff", json={"items": [{"kind": "upkeep", "id": up["id"]}], "assignee_id": vanessa}, headers=susan_headers)
    assert r.status_code == 403 and "admin" in r.json()["detail"].lower()
    r = client.post("/api/list/handoff", json={"items": [{"kind": "upkeep", "id": up["id"]}], "assignee_id": susan}, headers=susan_headers)
    assert r.status_code == 200
    assert len(_items(client, susan_headers, "upkeep", id=up["id"])) == 1
    client.delete(f"/api/upkeep/{up['id']}", headers=admin_headers)


def test_mcp_hand_off(mcp, client, admin_headers, susan_headers):  # noqa: F811
    up = client.post("/api/upkeep", json={"name": "Handoff dryer vent", "interval_days": 180}, headers=admin_headers).json()
    before = len(_notes(client, susan_headers))
    res = mcp("hand_off", person="susan", kind="upkeep", items=["dryer vent"], idempotency_key="handoff-1")
    assert res["handed_to"] == "Susan" and res["items"][0]["id"] == up["id"]
    assert "Susan" in res["message"]
    assert len(_notes(client, susan_headers)) == before + 1
    again = mcp("hand_off", person="susan", kind="upkeep", items=["dryer vent"], idempotency_key="handoff-1")
    assert again.get("replayed") is True
    assert len(_notes(client, susan_headers)) == before + 1

    lst = mcp("list_for_person", person="susan")
    assert any(i["kind"] == "upkeep" and i["id"] == up["id"] for i in lst["items"])
    assert not any(i["kind"] == "upkeep" and i["id"] == up["id"] for i in mcp("list_for_person", person="courtney")["items"])

    res = mcp("hand_off", person="Bob", kind="upkeep", items=["dryer vent"])
    assert res.get("unknown_person") == "Bob"
    with pytest.raises(ToolError, match="task, upkeep, refill, declutter"):
        mcp("hand_off", person="susan", kind="laundry", items=["x"])
    with get_conn() as conn:
        conn.execute("DELETE FROM upkeep_items WHERE id = ?", (up["id"],))
