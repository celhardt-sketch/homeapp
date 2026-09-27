"""Named people, per-person lists, attribution, assignment notifications, and the two MCP tools."""

from datetime import date, timedelta

from app.db import get_conn
from tests.conftest import user_id
from tests.test_mcp import _unwrap, mcp, server_url  # noqa: F401  (fixtures)


def _notifications_for(client, headers):
    return client.get("/api/me/notifications", headers=headers).json()


def test_completion_records_which_user(client, admin_headers, susan_headers, vanessa_headers):
    room = client.get("/api/rooms", headers=admin_headers).json()[0]
    task = client.post("/api/tasks", json={"room_id": room["id"], "title": "Who did it", "frequency_days": 7}, headers=admin_headers).json()
    # the name typed in the body is ignored for a named user: attribution is the logged-in person
    r = client.post(f"/api/tasks/{task['id']}/complete", json={"completed_by": "Somebody Else"}, headers=vanessa_headers)
    assert r.status_code == 201
    assert r.json()["last_completed_by"] == "Vanessa"
    history = client.get(f"/api/tasks/{task['id']}/history", headers=susan_headers).json()
    assert history[0]["completed_by"] == "Vanessa"
    assert history[0]["user_id"] == user_id(client, admin_headers, "Vanessa")
    activity = client.get("/api/activity", headers=admin_headers).json()
    assert any(a["task_title"] == "Who did it" and a["completed_by"] == "Vanessa" and a["user_id"] for a in activity)


def test_upkeep_log_records_which_user(client, admin_headers, susan_headers):
    up = client.post("/api/upkeep", json={"name": "Attribution filter", "interval_days": 90}, headers=admin_headers).json()
    r = client.post(f"/api/upkeep/{up['id']}/logs", json={"done_on": date.today().isoformat()}, headers=susan_headers)
    assert r.status_code == 201
    logs = client.get(f"/api/upkeep/{up['id']}/logs", headers=admin_headers).json()
    assert logs[0]["done_by"] == "Susan" and logs[0]["user_id"] == user_id(client, admin_headers, "Susan")


def test_standalone_task_with_assignee_shows_on_their_list(client, admin_headers, susan_headers):
    susan = user_id(client, admin_headers, "Susan")
    r = client.post(
        "/api/tasks",
        json={"title": "Return library books", "assignee_id": susan, "due_on": (date.today() - timedelta(days=1)).isoformat()},
        headers=admin_headers,
    )
    assert r.status_code == 201, r.text
    task = r.json()
    assert task["room_id"] is None and task["room_name"] is None and task["assignee"] == "Susan"
    mine = client.get("/api/me/list", headers=susan_headers).json()
    assert mine["user"]["name"] == "Susan"
    item = next(i for i in mine["items"] if i["kind"] == "task" and i["id"] == task["id"])
    assert item["overdue"] is True and item["room_name"] is None
    assert mine["overdue_count"] >= 1
    # oldest first
    created = [i["created_at"] for i in mine["items"]]
    assert created == sorted(created)
    # anyone logged in may read someone else's list
    other = client.get(f"/api/users/{susan}/list", headers=admin_headers).json()
    assert any(i["id"] == task["id"] for i in other["items"])
    # Susan can complete her own item; it then leaves the list
    assert client.post(f"/api/tasks/{task['id']}/complete", json={}, headers=susan_headers).status_code == 201
    assert not any(i["kind"] == "task" and i["id"] == task["id"] for i in client.get("/api/me/list", headers=susan_headers).json()["items"])
    assert any(i["id"] == task["id"] for i in client.get("/api/me/list?include_done=true", headers=susan_headers).json()["items"])


def test_assignment_creates_exactly_one_notification(client, admin_headers, vanessa_headers):
    vanessa = user_id(client, admin_headers, "Vanessa")
    before = len(_notifications_for(client, vanessa_headers))
    task = client.post("/api/tasks", json={"title": "Buy birthday candles", "assignee_id": vanessa}, headers=admin_headers).json()
    after = _notifications_for(client, vanessa_headers)
    assert len(after) == before + 1
    n = after[0]
    assert n["kind"] == "task" and n["ref_id"] == task["id"] and n["created_by"] == "Courtney" and n["read_at"] is None
    # editing the task without changing the assignee does not notify again
    client.patch(f"/api/tasks/{task['id']}", json={"title": "Buy birthday candles (blue)"}, headers=admin_headers)
    client.patch(f"/api/tasks/{task['id']}", json={"assignee_id": vanessa}, headers=admin_headers)
    assert len(_notifications_for(client, vanessa_headers)) == before + 1
    with get_conn() as conn:
        count = conn.execute("SELECT COUNT(*) FROM notifications WHERE kind = 'task' AND ref_id = ?", (task["id"],)).fetchone()[0]
    assert count == 1
    assert client.post("/api/me/notifications/read", headers=vanessa_headers).json()["marked"] >= 1
    assert _notifications_for(client, vanessa_headers)[0]["read_at"] is not None


def test_shopping_item_with_assignee(client, admin_headers, susan_headers):
    susan = user_id(client, admin_headers, "Susan")
    before = len(_notifications_for(client, susan_headers))
    r = client.post("/api/shopping-items", json={"name": "Dish soap", "assignee_id": susan}, headers=admin_headers)
    assert r.status_code == 201, r.text
    item = r.json()
    assert item["assignee"] == "Susan"
    assert len(_notifications_for(client, susan_headers)) == before + 1
    assert any(i["kind"] == "shopping" and i["id"] == item["id"] for i in client.get("/api/me/list", headers=susan_headers).json()["items"])
    assert any(s["id"] == item["id"] for s in client.get("/api/shopping-items", headers=susan_headers).json())
    r = client.patch(f"/api/shopping-items/{item['id']}", json={"bought": True}, headers=susan_headers)
    assert r.status_code == 200 and r.json()["bought_by"] == "Susan" and r.json()["done"] is True


def test_member_cannot_reassign_but_can_self_assign(client, admin_headers, susan_headers, vanessa_headers):
    susan = user_id(client, admin_headers, "Susan")
    vanessa = user_id(client, admin_headers, "Vanessa")
    r = client.post("/api/tasks", json={"title": "Susan's own errand", "assignee_id": susan}, headers=susan_headers)
    assert r.status_code == 201
    assert client.post("/api/tasks", json={"title": "Pushed onto Vanessa", "assignee_id": vanessa}, headers=susan_headers).status_code == 403
    task = client.post("/api/tasks", json={"title": "Assigned by admin", "assignee_id": vanessa}, headers=admin_headers).json()
    assert client.patch(f"/api/tasks/{task['id']}", json={"assignee_id": susan}, headers=susan_headers).status_code == 403
    assert client.patch(f"/api/tasks/{task['id']}", json={"assignee_id": None}, headers=vanessa_headers).status_code == 403
    assert client.patch(f"/api/tasks/{task['id']}", json={"assignee_id": susan}, headers=admin_headers).status_code == 200


def test_connector_can_assign_and_read_lists(client, connector_headers, admin_headers, susan_headers):
    susan = user_id(client, admin_headers, "Susan")
    before = len(_notifications_for(client, susan_headers))
    r = client.post("/api/tasks", json={"title": "From Claude", "assignee_id": susan}, headers=connector_headers)
    assert r.status_code == 201 and r.json()["assignee"] == "Susan"
    r = client.get(f"/api/users/{susan}/list", headers=connector_headers)
    assert r.status_code == 200 and any(i["title"] == "From Claude" for i in r.json()["items"])
    notes = _notifications_for(client, susan_headers)
    assert len(notes) == before + 1 and notes[0]["created_by"] == "Claude"


def test_admin_user_management(client, admin_headers, susan_headers):
    assert client.post("/api/admin/users", json={"name": "Nanny", "password": "nanny-pass"}, headers=susan_headers).status_code == 403
    r = client.post("/api/admin/users", json={"name": "Nanny", "password": "nanny-pass"}, headers=admin_headers)
    assert r.status_code == 201 and r.json()["role"] == "member"
    nid = r.json()["id"]
    assert client.post("/api/admin/users", json={"name": "nanny", "password": "x" * 8}, headers=admin_headers).status_code == 409
    assert client.post("/api/login", json={"name": "nanny", "password": "nanny-pass"}).status_code == 200
    assert "Nanny" in client.get("/api/login/names").json()
    assert client.patch(f"/api/admin/users/{nid}", json={"active": False}, headers=admin_headers).status_code == 200
    assert client.post("/api/login", json={"name": "Nanny", "password": "nanny-pass"}).status_code == 401
    assert "Nanny" not in client.get("/api/login/names").json()
    me = user_id(client, admin_headers, "Courtney")
    assert client.patch(f"/api/admin/users/{me}", json={"role": "member"}, headers=admin_headers).status_code == 400
    assert client.delete(f"/api/admin/users/{me}", headers=admin_headers).status_code == 400
    assert client.delete(f"/api/admin/users/{nid}", headers=admin_headers).status_code == 204


# ---------- MCP ----------


def test_mcp_add_to_list_lowercase_susan(mcp, client, admin_headers, susan_headers):  # noqa: F811
    before = len(_notifications_for(client, susan_headers))
    res = mcp("add_to_list", person="susan", item="Return the library books", due_on="tomorrow")
    assert res["created"] is True and res["kind"] == "task" and res["assigned_to"] == "Susan"
    assert res["task"]["room"] is None
    assert res["task"]["due_on"] == (date.today() + timedelta(days=1)).isoformat()
    assert len(_notifications_for(client, susan_headers)) == before + 1

    res = mcp("add_to_list", person="SUSAN", item="Buy dish sponges")
    assert res["kind"] == "shopping" and res["assigned_to"] == "Susan"
    res = mcp("add_to_list", person="susan", item="Sponges again", kind="shopping", notes="the green ones")
    assert res["kind"] == "shopping" and res["shopping_item"]["notes"] == "the green ones"
    res = mcp("add_to_list", person="susan", item="Pick up the dry cleaning", kind="task")
    assert res["kind"] == "task"

    lst = mcp("list_for_person", person="susan")
    titles = [i["title"] for i in lst["items"]]
    assert {"Return the library books", "Buy dish sponges", "Pick up the dry cleaning"} <= set(titles)
    assert [i["created_at"] for i in lst["items"]] == sorted(i["created_at"] for i in lst["items"])
    assert all("overdue" in i for i in lst["items"])


def test_mcp_unknown_person_lists_people_and_creates_nothing(mcp, client, admin_headers):  # noqa: F811
    with get_conn() as conn:
        tasks_before = conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
        shop_before = conn.execute("SELECT COUNT(*) FROM shopping_items").fetchone()[0]
        notes_before = conn.execute("SELECT COUNT(*) FROM notifications").fetchone()[0]
    res = mcp("add_to_list", person="Susanna", item="Something")
    assert res["created"] is False and res["unknown_person"] == "Susanna"
    assert set(res["people"]) >= {"Courtney", "Magnus", "Susan", "Vanessa"}
    assert "Susan" in res["message"]
    res = mcp("list_for_person", person="Bob")
    assert res.get("unknown_person") == "Bob"
    with get_conn() as conn:
        assert conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == tasks_before
        assert conn.execute("SELECT COUNT(*) FROM shopping_items").fetchone()[0] == shop_before
        assert conn.execute("SELECT COUNT(*) FROM notifications").fetchone()[0] == notes_before
    with get_conn() as conn:
        assert conn.execute("SELECT COUNT(*) FROM users WHERE name IN ('Susanna', 'Bob')").fetchone()[0] == 0


def test_mcp_list_tasks_assignee_filter_and_add_task_assignee(mcp, client, admin_headers):  # noqa: F811
    res = mcp("add_task", title="Oil the front door hinges", assignee="vanessa")
    assert res["created"] is True and res["task"]["assignee"] == "Vanessa"
    tasks = _unwrap(mcp("list_tasks", assignee="Vanessa"))
    assert any(t["title"] == "Oil the front door hinges" for t in tasks)
    assert all(t["assignee"] == "Vanessa" for t in tasks)
    tasks = _unwrap(mcp("list_tasks", assignee="susan"))
    assert not any(t["title"] == "Oil the front door hinges" for t in tasks)
