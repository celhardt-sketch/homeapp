"""Room management: admin/connector only; duplicates, rename keeps id + history + old NFC slug,
archive hides but keeps, delete refused while tasks remain."""

from tests.test_mcp import _unwrap, mcp, server_url  # noqa: F401  (fixtures)


def test_create_dining_room_then_duplicate(client, admin_headers):
    r = client.post("/api/rooms", json={"name": "Dining Room", "icon": "utensils"}, headers=admin_headers)
    assert r.status_code == 201, r.text
    room = r.json()
    assert room["slug"] == "dining-room" and room["active"] is True and room["duplicate"] is False
    assert room["icon"] == "utensils"

    before = len(client.get("/api/rooms", headers=admin_headers).json())
    r = client.post("/api/rooms", json={"name": "dining room"}, headers=admin_headers)
    assert r.status_code == 200, r.text
    assert r.json()["duplicate"] is True and r.json()["id"] == room["id"]
    r = client.post("/api/rooms", json={"name": "Dinning Room"}, headers=admin_headers)
    assert r.json()["duplicate"] is True and r.json()["id"] == room["id"]
    assert len(client.get("/api/rooms", headers=admin_headers).json()) == before

    assert client.get(f"/api/rooms/{room['slug']}", headers=admin_headers).status_code == 200
    assert client.delete(f"/api/rooms/{room['id']}", headers=admin_headers).status_code == 204


def test_members_cannot_change_rooms(client, admin_headers, susan_headers, vanessa_headers):
    before = client.get("/api/rooms", headers=admin_headers).json()
    for h in (susan_headers, vanessa_headers):
        assert client.post("/api/rooms", json={"name": "Susan's Room"}, headers=h).status_code == 403
        assert client.patch(f"/api/rooms/{before[0]['id']}", json={"name": "Nope"}, headers=h).status_code == 403
        assert client.delete(f"/api/rooms/{before[0]['id']}", headers=h).status_code == 403
        assert client.get("/api/rooms", headers=h).status_code == 200
    assert client.get("/api/rooms", headers=admin_headers).json() == before


def test_rename_keeps_id_history_and_old_nfc_slug(client, admin_headers, susan_headers):
    room = client.post("/api/rooms", json={"name": "Sun Porch"}, headers=admin_headers).json()
    task = client.post(
        "/api/tasks", json={"room_id": room["id"], "title": "Water the ferns", "frequency_days": 3}, headers=admin_headers
    ).json()
    assert client.post(f"/api/tasks/{task['id']}/complete", json={}, headers=susan_headers).status_code == 201

    r = client.patch(f"/api/rooms/{room['id']}", json={"name": "Conservatory"}, headers=admin_headers)
    assert r.status_code == 200, r.text
    renamed = r.json()
    assert renamed["id"] == room["id"]
    assert renamed["name"] == "Conservatory" and renamed["slug"] == "conservatory"
    assert [t["id"] for t in renamed["tasks"]] == [task["id"]]
    assert renamed["tasks"][0]["last_completed_by"] == "Susan"
    history = client.get(f"/api/tasks/{task['id']}/history", headers=admin_headers).json()
    assert len(history) == 1 and history[0]["completed_by"] == "Susan"

    # the NFC tag on the wall still says /r/sun-porch
    old = client.get("/api/rooms/sun-porch", headers=susan_headers)
    assert old.status_code == 200
    assert old.json()["id"] == room["id"] and old.json()["slug"] == "conservatory"
    assert client.get("/api/rooms/conservatory", headers=susan_headers).json()["id"] == room["id"]

    # renaming again keeps both older slugs working
    client.patch(f"/api/rooms/{room['id']}", json={"name": "Garden Room"}, headers=admin_headers)
    for slug in ("sun-porch", "conservatory", "garden-room"):
        assert client.get(f"/api/rooms/{slug}", headers=susan_headers).json()["id"] == room["id"]
    # renaming back to a previous name reclaims that slug (no "-2")
    back = client.patch(f"/api/rooms/{room['id']}", json={"name": "Sun Porch"}, headers=admin_headers).json()
    assert back["slug"] == "sun-porch"
    # a new room can't take a slug an old tag still uses
    other = client.post("/api/rooms", json={"name": "Garden Room"}, headers=admin_headers).json()
    assert other["duplicate"] is False and other["slug"] == "garden-room-2"
    assert client.get("/api/rooms/garden-room", headers=susan_headers).json()["id"] == room["id"]


def test_delete_refused_while_tasks_remain(client, admin_headers):
    room = client.post("/api/rooms", json={"name": "Wine Cellar"}, headers=admin_headers).json()
    for title in ("Check humidity", "Rotate bottles", "Dust racks"):
        client.post("/api/tasks", json={"room_id": room["id"], "title": title}, headers=admin_headers)
    r = client.delete(f"/api/rooms/{room['id']}", headers=admin_headers)
    assert r.status_code == 409
    msg = r.json()["detail"]
    assert "Wine Cellar" in msg and "3 tasks" in msg and "eactivate" in msg
    assert client.get(f"/api/rooms/{room['slug']}", headers=admin_headers).json()["task_count"] == 3


def test_archive_hides_room_and_its_tasks_but_keeps_history(client, admin_headers, susan_headers):
    room = client.post("/api/rooms", json={"name": "Guest Room"}, headers=admin_headers).json()
    task = client.post(
        "/api/tasks", json={"room_id": room["id"], "title": "Air the guest bed", "frequency_days": 1}, headers=admin_headers
    ).json()
    client.post(f"/api/tasks/{task['id']}/complete", json={}, headers=susan_headers)
    status = client.get("/api/status", headers=susan_headers).json()
    assert any(t["id"] == task["id"] for sec in ("tasks_overdue", "tasks_due_today", "tasks_due_within_7_days") for t in status[sec])

    r = client.patch(f"/api/rooms/{room['id']}", json={"active": False}, headers=admin_headers)
    assert r.status_code == 200 and r.json()["active"] is False

    assert room["id"] not in {x["id"] for x in client.get("/api/rooms", headers=susan_headers).json()}
    assert room["id"] in {x["id"] for x in client.get("/api/rooms?include_archived=true", headers=admin_headers).json()}
    status = client.get("/api/status", headers=susan_headers).json()
    assert not any(t["id"] == task["id"] for sec in ("tasks_overdue", "tasks_due_today", "tasks_due_within_7_days") for t in status[sec])
    # history kept, and the tag still opens the (archived) room
    history = client.get(f"/api/tasks/{task['id']}/history", headers=admin_headers).json()
    assert len(history) == 1 and history[0]["completed_by"] == "Susan"
    old = client.get("/api/rooms/guest-room", headers=susan_headers).json()
    assert old["id"] == room["id"] and old["active"] is False and old["task_count"] == 1

    # an archived room's name is free to reuse... but reactivating brings it back as-is
    assert client.patch(f"/api/rooms/{room['id']}", json={"active": True}, headers=admin_headers).json()["active"] is True
    assert room["id"] in {x["id"] for x in client.get("/api/rooms", headers=susan_headers).json()}


def test_mcp_room_tools(mcp, client, admin_headers, susan_headers):  # noqa: F811
    res = mcp("add_room", name="Mud Room", icon="home")
    assert res["created"] is True and res["room"]["slug"] == "mud-room"
    rid = res["room"]["id"]
    dup = mcp("add_room", name="mud room")
    assert dup["created"] is False and dup["duplicate"] is True and dup["room"]["id"] == rid

    task = client.post("/api/tasks", json={"room_id": rid, "title": "Sweep out sand"}, headers=admin_headers).json()
    client.post(f"/api/tasks/{task['id']}/complete", json={}, headers=susan_headers)

    res = mcp("rename_room", room="mud room", new_name="Boot Room")
    assert res["renamed"] is True and res["room"]["id"] == rid and res["room"]["slug"] == "boot-room"
    assert res["old_slug"] == "mud-room"
    assert client.get("/api/rooms/mud-room", headers=susan_headers).json()["id"] == rid
    assert client.get(f"/api/tasks/{task['id']}/history", headers=admin_headers).json()[0]["completed_by"] == "Susan"
    assert any(r["id"] == rid and r["name"] == "Boot Room" for r in _unwrap(mcp("list_rooms")))

    res = mcp("archive_room", room=str(rid))
    assert res["archived"] is True and res["room"]["id"] == rid
    assert not any(r["id"] == rid for r in _unwrap(mcp("list_rooms")))
    assert client.get("/api/rooms/boot-room", headers=susan_headers).json()["active"] is False
    assert client.get(f"/api/tasks/{task['id']}/history", headers=admin_headers).json()[0]["completed_by"] == "Susan"


def test_startup_removes_accidental_duplicate_laundry_room(client, admin_headers):
    """Production had id 3 'Laundry Room' (slug laundry, 4 tasks) and id 9 'Laundry Room'
    (slug laundry-room, no tasks) from before the duplicate check. init_db deletes the empty one,
    keeps its slug as an alias, and the check now refuses to recreate it."""
    from app.db import get_conn, init_db

    with get_conn() as conn:
        conn.execute("DELETE FROM rooms WHERE id = 9")
        laundry = conn.execute("SELECT * FROM rooms WHERE slug = 'laundry'").fetchone()
        assert laundry and laundry["id"] == 3
        assert conn.execute("SELECT COUNT(*) FROM tasks WHERE room_id = 3").fetchone()[0] == 4
        conn.execute("INSERT INTO rooms (id, slug, name, icon, sort_order) VALUES (9, 'laundry-room', 'Laundry Room', 'home', 99)")
    init_db()
    with get_conn() as conn:
        assert conn.execute("SELECT 1 FROM rooms WHERE id = 9").fetchone() is None
        assert conn.execute("SELECT COUNT(*) FROM rooms WHERE lower(name) = 'laundry room'").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM tasks WHERE room_id = 3").fetchone()[0] == 4

    assert client.get("/api/rooms/laundry-room", headers=admin_headers).json()["id"] == 3
    r = client.post("/api/rooms", json={"name": "Laundry Room"}, headers=admin_headers)
    assert r.status_code == 200 and r.json()["duplicate"] is True and r.json()["id"] == 3
