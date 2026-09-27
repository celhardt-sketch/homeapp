"""Declutter list: drawers, closets and boxes to go through, checked off with who and when."""

from datetime import date, timedelta

import pytest

from tests.test_mcp import mcp, server_url  # noqa: F401  (fixtures)


@pytest.fixture
def clean(client, admin_headers):
    def wipe():
        for s in client.get("/api/declutter?include_done=true", headers=admin_headers).json():
            client.delete(f"/api/declutter/{s['id']}", headers=admin_headers)

    wipe()
    yield
    wipe()


def _room(client, headers, name):
    return next(r for r in client.get("/api/rooms", headers=headers).json() if r["name"] == name)


def test_unauthenticated_rejected(client):
    assert client.get("/api/declutter").status_code == 401
    assert client.post("/api/declutter", json={"name": "Junk drawer"}).status_code == 401


def test_helper_adds_completes_reopens_deletes(client, susan_headers, clean):
    kitchen = _room(client, susan_headers, "Kitchen")
    r = client.post("/api/declutter", json={"name": "  Junk   drawer ", "room_id": kitchen["id"], "notes": "top left"}, headers=susan_headers)
    assert r.status_code == 201
    spot = r.json()
    assert spot["name"] == "Junk drawer"
    assert spot["room"] == "Kitchen"
    assert spot["done"] is False
    assert spot["duplicate"] is False
    assert spot["created_by"] == "Susan"

    listed = client.get("/api/declutter", headers=susan_headers).json()
    assert [s["id"] for s in listed] == [spot["id"]]

    r = client.patch(f"/api/declutter/{spot['id']}", json={"done": True, "notes": "donated a bag"}, headers=susan_headers)
    assert r.status_code == 200
    done = r.json()
    assert done["done"] is True
    assert done["done_on"] == date.today().isoformat()
    assert done["done_by"] == "Susan"
    assert done["days_since_done"] == 0
    assert done["notes"] == "donated a bag"

    assert client.get("/api/declutter", headers=susan_headers).json() == []
    with_done = client.get("/api/declutter?include_done=true", headers=susan_headers).json()
    assert [s["id"] for s in with_done] == [spot["id"]]

    reopened = client.patch(f"/api/declutter/{spot['id']}", json={"done": False}, headers=susan_headers).json()
    assert reopened["done"] is False and reopened["done_on"] is None and reopened["done_by"] is None

    assert client.delete(f"/api/declutter/{spot['id']}", headers=susan_headers).status_code == 204
    assert client.get(f"/api/declutter?include_done=true", headers=susan_headers).json() == []


def test_batch_and_duplicate(client, household_headers, clean):
    kitchen = _room(client, household_headers, "Kitchen")
    r = client.post(
        "/api/declutter",
        json=[
            {"name": "Junk drawer", "room_id": kitchen["id"]},
            {"name": "Hall closet"},
            {"name": "Boxes under the stairs"},
        ],
        headers=household_headers,
    )
    assert r.status_code == 201
    rows = r.json()
    assert len(rows) == 3 and all(not s["duplicate"] for s in rows)

    # same room, near-identical name -> existing row, nothing inserted
    dup = client.post("/api/declutter", json={"name": "junk drawers", "room_id": kitchen["id"]}, headers=household_headers).json()
    assert dup["duplicate"] is True and dup["id"] == rows[0]["id"]
    # a junk drawer in another room is a different spot
    girls = _room(client, household_headers, "Laundry Room")
    other = client.post("/api/declutter", json={"name": "Junk drawer", "room_id": girls["id"]}, headers=household_headers).json()
    assert other["duplicate"] is False
    assert len(client.get("/api/declutter", headers=household_headers).json()) == 4

    assert client.post("/api/declutter", json=[], headers=household_headers).status_code == 422
    assert client.post("/api/declutter", json={"name": "X", "room_id": 99999}, headers=household_headers).status_code == 404


def test_explicit_done_on_and_room_filter(client, household_headers, clean):
    kitchen = _room(client, household_headers, "Kitchen")
    a = client.post("/api/declutter", json={"name": "Spice cabinet", "room_id": kitchen["id"]}, headers=household_headers).json()
    client.post("/api/declutter", json={"name": "Garage shelves"}, headers=household_headers)
    assert [s["id"] for s in client.get(f"/api/declutter?room_id={kitchen['id']}", headers=household_headers).json()] == [a["id"]]
    when = date.today() - timedelta(days=12)
    done = client.patch(f"/api/declutter/{a['id']}", json={"done": True, "done_on": when.isoformat()}, headers=household_headers).json()
    assert done["done_on"] == when.isoformat() and done["days_since_done"] == 12


# ---------- MCP ----------


def test_mcp_add_list_done(mcp, client, household_headers, clean):
    res = mcp(
        "add_declutter_spots",
        spots=[{"name": "Junk drawer", "room": "kitchen"}, {"name": "Hall closet", "notes": "top shelf"}],
        idempotency_key="declutter-1",
    )
    assert len(res["added"]) == 2 and res["duplicates"] == []
    assert res["added"][0]["room"] == "Kitchen"
    replay = mcp("add_declutter_spots", spots=[{"name": "Junk drawer", "room": "kitchen"}], idempotency_key="declutter-1")
    assert replay["replayed"] is True
    again = mcp("add_declutter_spots", spots=[{"name": "junk drawers", "room": "Kitchen"}])
    assert again["added"] == [] and len(again["duplicates"]) == 1
    assert len(client.get("/api/declutter", headers=household_headers).json()) == 2

    listed = mcp("list_declutter")
    assert listed["open"] == 2
    assert mcp("list_declutter", room="kitchen")["open"] == 1

    res = mcp("declutter_done", spot="hall closet", done_by="Susan", notes="two bags donated")
    assert res["done"] is True
    assert res["spot"]["done_by"] == "Susan" and res["spot"]["done_on"] == date.today().isoformat()
    assert res["spot"]["notes"] == "two bags donated"
    assert mcp("list_declutter")["open"] == 1
    assert mcp("list_declutter", include_done=True)["spots"][-1]["name"] == "Hall closet"
    # default attribution is the connector's name
    res = mcp("declutter_done", spot="junk drawer")
    assert res["spot"]["done_by"] == "Claude"


def test_mcp_ambiguous_changes_nothing_and_no_match_lists_nearest(mcp, client, household_headers, clean):
    kitchen = _room(client, household_headers, "Kitchen")
    girls = _room(client, household_headers, "Laundry Room")
    client.post(
        "/api/declutter",
        json=[{"name": "Junk drawer", "room_id": kitchen["id"]}, {"name": "Junk drawer", "room_id": girls["id"]}],
        headers=household_headers,
    )
    res = mcp("declutter_done", spot="junk drawer")
    assert res["needs_disambiguation"] is True
    assert {c["room"] for c in res["candidates"]} == {"Kitchen", "Laundry Room"}
    assert all(not s["done"] for s in client.get("/api/declutter", headers=household_headers).json())

    res = mcp("declutter_done", spot="junk drawer laundry")
    assert res["done"] is True and res["spot"]["room"] == "Laundry Room"

    with pytest.raises(Exception) as exc:
        mcp("declutter_done", spot="attic")
    assert "No declutter spot called 'attic'" in str(exc.value)
    assert "Junk drawer" in str(exc.value)

    with pytest.raises(Exception) as exc:
        mcp("add_declutter_spots", spots=[{"name": "Toy bin", "room": "attic"}])
    assert "No room called 'attic'" in str(exc.value)


def test_find_includes_declutter(mcp, client, household_headers, clean):
    client.post("/api/declutter", json={"name": "Linen closet"}, headers=household_headers)
    assert any(s["name"] == "Linen closet" for s in mcp("find", query="linen")["declutter"])
