"""Every /api route (except the allow-list) must reject unauthenticated requests with 401."""

import pytest

from app.main import PUBLIC_API_PATHS, app

# (method, path, json body) — one representative read and write per route family.
PROTECTED = [
    # rooms
    ("GET", "/api/rooms", None),
    ("GET", "/api/rooms/kitchen", None),
    ("POST", "/api/rooms", {"name": "X"}),
    ("PATCH", "/api/rooms/1", {"name": "X"}),
    ("DELETE", "/api/rooms/1", None),
    # tasks
    ("POST", "/api/tasks", {"room_id": 1, "title": "X"}),
    ("PATCH", "/api/tasks/1", {"title": "X"}),
    ("DELETE", "/api/tasks/1", None),
    ("GET", "/api/tasks/1/history", None),
    # completions
    ("POST", "/api/tasks/1/complete", {"completed_by": "X"}),
    ("DELETE", "/api/completions/1", None),
    # notes
    ("POST", "/api/tasks/1/notes", {"text": "X"}),
    ("PATCH", "/api/notes/1", {"resolved": True}),
    # shopping / activity
    ("GET", "/api/shopping", None),
    ("GET", "/api/activity", None),
    # pantry
    ("GET", "/api/pantry", None),
    ("POST", "/api/pantry", {"name": "X"}),
    ("PATCH", "/api/pantry/1", {"name": "X"}),
    ("DELETE", "/api/pantry/1", None),
    # medications
    ("GET", "/api/medications", None),
    ("POST", "/api/medications", {"name": "X", "person": "X"}),
    ("PATCH", "/api/medications/1", {"name": "X"}),
    ("DELETE", "/api/medications/1", None),
    ("POST", "/api/medications/1/pickups", {"picked_up_on": "2026-01-01"}),
    ("GET", "/api/medications/1/pickups", None),
    ("DELETE", "/api/pickups/1", None),
    # upkeep
    ("GET", "/api/upkeep", None),
    ("POST", "/api/upkeep", {"name": "X", "interval_days": 30}),
    ("PATCH", "/api/upkeep/1", {"name": "X"}),
    ("DELETE", "/api/upkeep/1", None),
    ("POST", "/api/upkeep/1/logs", {"done_on": "2026-01-01"}),
    ("GET", "/api/upkeep/1/logs", None),
    ("DELETE", "/api/upkeep-logs/1", None),
    # admin
    ("GET", "/api/admin/me", None),
    ("POST", "/api/admin/password", {"current": "x", "new": "yyyyyyyy"}),
    ("GET", "/api/admin/reminders", None),
    ("PUT", "/api/admin/reminders", {"reminder_email": ""}),
    ("POST", "/api/admin/reminders/test", None),
    # routes that don't exist yet are still denied
    ("GET", "/api/some/future/route", None),
]


@pytest.mark.parametrize("method,path,body", PROTECTED, ids=lambda v: v if isinstance(v, str) else "")
def test_unauthenticated_is_401(client, method, path, body):
    r = client.request(method, path, json=body)
    assert r.status_code == 401, f"{method} {path} -> {r.status_code}"


@pytest.mark.parametrize("method,path,body", PROTECTED, ids=lambda v: v if isinstance(v, str) else "")
def test_bad_token_is_401(client, method, path, body):
    r = client.request(method, path, json=body, headers={"Authorization": "Bearer not.a.token"})
    assert r.status_code == 401


def test_every_registered_api_route_is_covered():
    """Guard against a new route family being added without a test above."""
    registered = {
        route.path for route in app.routes if getattr(route, "path", "").startswith("/api/")
    } - PUBLIC_API_PATHS
    tested_prefixes = {"/api/" + p.split("/")[2] for _, p, _ in PROTECTED}
    missing = {p for p in registered if "/api/" + p.split("/")[2] not in tested_prefixes}
    assert not missing, f"untested route families: {sorted(missing)}"


def test_allow_list_is_open(client):
    assert client.get("/api/health").status_code == 200
    assert client.post("/api/admin/login", json={"password": "wrong"}).status_code == 401
    assert client.post("/api/admin/login", json={"password": "test-password"}).status_code == 200


def test_authenticated_read_and_write(client, auth_headers):
    assert client.get("/api/rooms", headers=auth_headers).status_code == 200
    r = client.post("/api/upkeep", json={"name": "Test", "interval_days": 30}, headers=auth_headers)
    assert r.status_code == 201
    assert client.delete(f"/api/upkeep/{r.json()['id']}", headers=auth_headers).status_code == 204


def test_spa_and_room_pages_are_not_gated(client):
    for path in ("/", "/r/kitchen", "/upkeep"):
        assert client.get(path).status_code in (200, 404)  # 404 only when frontend isn't built
