"""Auth coverage for the whole /api surface.

- No credentials -> 401 on every route except the allow-list.
- Member (named non-admin user) session -> 403 on every admin-only route (admin/*, activity) and manager route.
- Member and connector -> 403 on room changes are covered in test_rooms.py; here MANAGER routes are
  checked for the connector (allowed) and member (denied).
- Every registered /api route declares exactly one role dependency.
"""

import pytest
from fastapi.routing import APIRoute

from app import auth
from app.main import PUBLIC_API_PATHS, SESSION_TOKEN_HEADER, app
from tests.conftest import headers_for

# (method, path, json body) — one representative read and write per route family.
HOUSEHOLD_OK = [
    ("GET", "/api/session", None),
    ("GET", "/api/status", None),
    ("GET", "/api/users", None),
    # rooms (read only; changes are MANAGER)
    ("GET", "/api/rooms", None),
    ("GET", "/api/rooms/kitchen", None),
    # tasks
    ("POST", "/api/tasks", {"room_id": 1, "title": "X"}),
    ("PATCH", "/api/tasks/1", {"title": "X"}),
    ("DELETE", "/api/tasks/1", None),
    ("GET", "/api/tasks/1/history", None),
    # completions
    ("POST", "/api/tasks/1/complete", {"completed_by": "X"}),
    ("DELETE", "/api/completions/1", None),
    # notes
    ("POST", "/api/tasks/1/notes", {"author": "X", "body": "X"}),
    ("PATCH", "/api/notes/1", {"resolved": True}),
    # shopping
    ("GET", "/api/shopping", None),
    # pantry
    ("GET", "/api/pantry", None),
    ("POST", "/api/pantry", {"name": "X"}),
    ("PATCH", "/api/pantry/1", {"name": "X"}),
    ("DELETE", "/api/pantry/1", None),
    # upkeep
    ("GET", "/api/upkeep", None),
    ("POST", "/api/upkeep", {"name": "X", "interval_days": 30}),
    ("PATCH", "/api/upkeep/1", {"name": "X"}),
    ("DELETE", "/api/upkeep/1", None),
    ("POST", "/api/upkeep/1/logs", {"done_on": "2026-01-01"}),
    ("GET", "/api/upkeep/1/logs", None),
    ("DELETE", "/api/upkeep-logs/1", None),
    # declutter
    ("GET", "/api/declutter", None),
    ("POST", "/api/declutter", {"name": "X"}),
    ("PATCH", "/api/declutter/1", {"name": "X"}),
    ("DELETE", "/api/declutter/1", None),
    # prescription refills (every named user can read, log pickups and mark called)
    ("GET", "/api/children", None),
    ("GET", "/api/prescriptions", None),
    ("GET", "/api/refills", None),
    ("POST", "/api/prescriptions/1/pickups", {"picked_up_on": "2026-01-01"}),
    ("GET", "/api/prescriptions/1/pickups", None),
    ("DELETE", "/api/pickups/1", None),
    ("POST", "/api/prescriptions/1/called", {}),
]

# admin or connector, never a member
MANAGER_ONLY = [
    ("POST", "/api/rooms", {"name": "X"}),
    ("PATCH", "/api/rooms/1", {"icon": "home"}),
    ("DELETE", "/api/rooms/999999", None),
    ("GET", "/api/activity", None),
    # helpers can't add, edit or deactivate children and prescriptions; admins and the connector can
    ("POST", "/api/children", {"name": "X"}),
    ("PATCH", "/api/children/1", {"name": "X"}),
    ("POST", "/api/prescriptions", {"child_id": 1, "name": "X"}),
    ("PATCH", "/api/prescriptions/1", {"name": "X"}),
]

ADMIN_ONLY = [
    ("POST", "/api/admin/users", {"name": "X", "password": "yyyyyyyy"}),
    ("PATCH", "/api/admin/users/1", {"email": ""}),
    ("DELETE", "/api/admin/users/1", None),
    ("GET", "/api/admin/reminders", None),
    ("PUT", "/api/admin/reminders", {"reminder_email": ""}),
    ("POST", "/api/admin/reminders/test", None),
    ("GET", "/api/admin/connector", None),
    ("DELETE", "/api/admin/connector", None),
]

ALL_PROTECTED = HOUSEHOLD_OK + MANAGER_ONLY + ADMIN_ONLY + [("GET", "/api/some/future/route", None)]


def _id(v):
    return v if isinstance(v, str) else ""


@pytest.mark.parametrize("method,path,body", ALL_PROTECTED, ids=_id)
def test_unauthenticated_is_401(client, method, path, body):
    r = client.request(method, path, json=body)
    assert r.status_code == 401, f"{method} {path} -> {r.status_code}"


@pytest.mark.parametrize("method,path,body", ALL_PROTECTED, ids=_id)
def test_bad_token_is_401(client, method, path, body):
    r = client.request(method, path, json=body, headers={"Authorization": "Bearer not.a.token"})
    assert r.status_code == 401


@pytest.mark.parametrize("method,path,body", ADMIN_ONLY + MANAGER_ONLY, ids=_id)
def test_household_is_403_on_admin_routes(client, household_headers, method, path, body):
    r = client.request(method, path, json=body, headers=household_headers)
    assert r.status_code == 403, f"{method} {path} -> {r.status_code}"


@pytest.mark.parametrize("method,path,body", MANAGER_ONLY, ids=_id)
def test_connector_may_change_rooms(client, connector_headers, method, path, body):
    r = client.request(method, path, json=body, headers=connector_headers)
    assert r.status_code not in (401, 403), f"{method} {path} -> {r.status_code}"


@pytest.mark.parametrize("method,path,body", ADMIN_ONLY, ids=_id)
def test_connector_is_403_on_admin_routes(client, connector_headers, method, path, body):
    """The MCP connector's OAuth token is rejected from every /api/admin route."""
    r = client.request(method, path, json=body, headers=connector_headers)
    assert r.status_code == 403, f"{method} {path} -> {r.status_code}"


@pytest.mark.parametrize("method,path,body", HOUSEHOLD_OK, ids=_id)
def test_connector_is_not_rejected_on_household_routes(client, connector_headers, method, path, body):
    r = client.request(method, path, json=body, headers=connector_headers)
    assert r.status_code not in (401, 403), f"{method} {path} -> {r.status_code}"
    assert SESSION_TOKEN_HEADER not in r.headers, "only household sessions slide"


@pytest.mark.parametrize("method,path,body", HOUSEHOLD_OK, ids=_id)
def test_household_is_not_rejected_on_household_routes(client, household_headers, method, path, body):
    r = client.request(method, path, json=body, headers=household_headers)
    assert r.status_code not in (401, 403), f"{method} {path} -> {r.status_code}"
    assert SESSION_TOKEN_HEADER in r.headers, "household session must renew on every request"


def test_every_api_route_declares_exactly_one_role():
    """A route added without HOUSEHOLD/ADMIN dependencies would be open to any logged-in role."""
    for route in app.routes:
        if not isinstance(route, APIRoute) or not route.path.startswith("/api/"):
            continue
        roles = [d.call for d in route.dependant.dependencies if d.call in auth.ROLE_DEPENDENCIES]
        if route.path in PUBLIC_API_PATHS:
            assert roles == [], f"{route.path} is public and must not declare a role"
        else:
            assert len(roles) == 1, f"{route.methods} {route.path} declares {len(roles)} roles"


def test_every_registered_admin_route_is_in_admin_only_list():
    listed = {(m, p) for m, p, _ in ADMIN_ONLY}
    for route in app.routes:
        if isinstance(route, APIRoute) and any(d.call is auth.need_admin for d in route.dependant.dependencies):
            for method in route.methods:
                sample = route.path.replace("{user_id}", "1").replace("{child_id}", "1").replace("{rx_id}", "1")
                assert (method, sample) in listed, f"admin route {method} {route.path} missing from ADMIN_ONLY"


def test_every_registered_manager_route_is_in_manager_only_list():
    listed = {(m, p.replace("999999", "1")) for m, p, _ in MANAGER_ONLY}
    for route in app.routes:
        if isinstance(route, APIRoute) and any(d.call is auth.need_manager for d in route.dependant.dependencies):
            for method in route.methods:
                sample = route.path.replace("{room_id}", "1").replace("{child_id}", "1").replace("{rx_id}", "1")
                assert (method, sample) in listed, f"manager route {method} {route.path} missing from MANAGER_ONLY"


def test_allow_list_is_open(client):
    assert client.get("/api/health").status_code == 200
    assert set(client.get("/api/login/names").json()) == {"Courtney", "Magnus", "Susan", "Vanessa"}
    assert client.post("/api/login", json={"name": "Susan", "password": "wrong"}).status_code == 401
    assert client.post("/api/login", json={"name": "Nobody", "password": "susan-test-password"}).status_code == 401
    assert client.post("/api/login", json={"password": "susan-test-password"}).status_code == 422
    r = client.post("/api/login", json={"name": "susan", "password": "susan-test-password"})
    assert r.status_code == 200 and r.json()["role"] == "member" and r.json()["user"]["name"] == "Susan"
    r = client.post("/api/login", json={"name": "Courtney", "password": "admin-test-password"})
    assert r.status_code == 200 and r.json()["role"] == "admin"
    r = client.post("/api/login", json={"name": "Magnus", "password": "magnus-test-password"})
    assert r.status_code == 200 and r.json()["role"] == "admin"


def test_each_person_has_their_own_password(client):
    assert client.post("/api/login", json={"name": "Vanessa", "password": "susan-test-password"}).status_code == 401
    assert client.post("/api/login", json={"name": "Vanessa", "password": "vanessa-test-password"}).status_code == 200


def test_connector_has_no_personal_list(client, connector_headers, household_headers):
    assert client.get("/api/me/list", headers=connector_headers).status_code == 403
    assert client.get("/api/me/list", headers=household_headers).status_code == 200


def test_admin_session_is_not_sliding(client, admin_headers):
    r = client.get("/api/rooms", headers=admin_headers)
    assert r.status_code == 200
    assert SESSION_TOKEN_HEADER not in r.headers


def test_household_renewed_token_is_valid_and_keeps_role(client, household_headers):
    renewed = client.get("/api/session", headers=household_headers).headers[SESSION_TOKEN_HEADER]
    r = client.get("/api/session", headers={"Authorization": f"Bearer {renewed}"})
    assert r.status_code == 200 and r.json()["role"] == "member" and r.json()["user"]["name"] == "Susan"
    assert client.get("/api/activity", headers={"Authorization": f"Bearer {renewed}"}).status_code == 403


def test_token_ttls():
    assert auth.TOKEN_TTL[auth.ADMIN] <= 30 * 24 * 3600
    assert auth.TOKEN_TTL[auth.MEMBER] == 365 * 24 * 3600


def test_admin_read_and_write(client, admin_headers):
    assert client.get("/api/activity", headers=admin_headers).status_code == 200
    r = client.post("/api/upkeep", json={"name": "Test", "interval_days": 30}, headers=admin_headers)
    assert r.status_code == 201
    assert client.delete(f"/api/upkeep/{r.json()['id']}", headers=admin_headers).status_code == 204


def test_admin_resetting_a_password_logs_only_that_person_out(client, admin_headers):
    vanessa = headers_for(client, "Vanessa")
    susan = headers_for(client, "Susan")
    vid = next(u["id"] for u in client.get("/api/users", headers=admin_headers).json() if u["name"] == "Vanessa")
    assert client.patch(f"/api/admin/users/{vid}", json={"password": "new-v-pass"}, headers=admin_headers).status_code == 200
    assert client.get("/api/session", headers=vanessa).status_code == 401
    assert client.get("/api/session", headers=susan).status_code == 200
    assert client.get("/api/session", headers=admin_headers).status_code == 200
    assert client.post("/api/login", json={"name": "Vanessa", "password": "vanessa-test-password"}).status_code == 401
    assert client.post("/api/login", json={"name": "Vanessa", "password": "new-v-pass"}).status_code == 200
    client.patch(f"/api/admin/users/{vid}", json={"password": "vanessa-test-password"}, headers=admin_headers)


def test_member_can_change_own_password_but_not_others(client, admin_headers):
    susan = headers_for(client, "Susan")
    vid = next(u["id"] for u in client.get("/api/users", headers=admin_headers).json() if u["name"] == "Vanessa")
    assert client.patch(f"/api/admin/users/{vid}", json={"password": "x" * 8}, headers=susan).status_code == 403
    r = client.post("/api/me/password", json={"current_password": "wrong", "new_password": "new-s-pass"}, headers=susan)
    assert r.status_code == 401
    r = client.post("/api/me/password", json={"current_password": "susan-test-password", "new_password": "new-s-pass"}, headers=susan)
    assert r.status_code == 200
    assert client.post("/api/login", json={"name": "Susan", "password": "new-s-pass"}).status_code == 200
    new = {"Authorization": f"Bearer {r.json()['token']}"}
    client.post("/api/me/password", json={"current_password": "new-s-pass", "new_password": "susan-test-password"}, headers=new)


def test_page_urls_never_write(client):
    """NFC tags point at page URLs; a bare GET must never create a completion."""
    get_paths = {r.path for r in app.routes if isinstance(r, APIRoute) and "GET" in r.methods}
    assert not any("complete" in p for p in get_paths)
    for path in ("/", "/r/kitchen", "/upkeep"):
        assert client.get(path).status_code in (200, 404)  # SPA shell only; 404 when frontend isn't built


def test_env_can_add_a_missing_user_without_touching_existing(client, monkeypatch):
    """HOME_USERS naming someone new creates them (password from PASSWORD_<NAME>); existing accounts,
    including passwords chosen in the app, are never overwritten by env vars."""
    from app.db import get_conn

    monkeypatch.setenv("HOME_USERS", "Courtney:admin,Magnus:admin,Susan,Vanessa,Grandma")
    monkeypatch.setenv("PASSWORD_GRANDMA", "grandma-pass")
    monkeypatch.setenv("PASSWORD_SUSAN", "should-be-ignored")
    auth.ensure_users()
    assert client.post("/api/login", json={"name": "Grandma", "password": "grandma-pass"}).json()["role"] == "member"
    assert client.post("/api/login", json={"name": "Susan", "password": "should-be-ignored"}).status_code == 401
    assert client.post("/api/login", json={"name": "Susan", "password": "susan-test-password"}).status_code == 200
    with get_conn() as conn:
        conn.execute("DELETE FROM users WHERE name = 'Grandma'")


def test_boot_fails_without_password_env_var(client, monkeypatch):
    """A HOME_USERS name with no stored account and no PASSWORD_<NAME> -> startup aborts naming
    the variable, and nothing is created."""
    from app.db import get_conn

    monkeypatch.setenv("HOME_USERS", "Courtney:admin,Magnus:admin,Susan,Vanessa,Aunt Jo")
    monkeypatch.delenv("PASSWORD_AUNT_JO", raising=False)
    with pytest.raises(auth.MissingPasswordError, match="PASSWORD_AUNT_JO"):
        auth.ensure_users()
    with get_conn() as conn:
        assert conn.execute("SELECT COUNT(*) FROM users WHERE name = 'Aunt Jo'").fetchone()[0] == 0


def test_boot_fails_without_password_env_var_via_app(client, monkeypatch, tmp_path):
    """Full app startup (lifespan) on a fresh database refuses to run and logs the missing variable."""
    import logging

    from fastapi.testclient import TestClient

    from app import db

    monkeypatch.setattr(db, "DATA_DIR", tmp_path)
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "fresh.db")
    monkeypatch.delenv("PASSWORD_VANESSA", raising=False)
    monkeypatch.delenv("HOUSEHOLD_PASSWORD", raising=False)
    records: list[logging.LogRecord] = []

    class Capture(logging.Handler):
        def emit(self, record):
            records.append(record)

    logging.getLogger("uvicorn.error").addHandler(handler := Capture())
    try:
        with pytest.raises(auth.MissingPasswordError):
            with TestClient(app):
                pass
    finally:
        logging.getLogger("uvicorn.error").removeHandler(handler)
    assert any(r.levelno == logging.CRITICAL and "PASSWORD_VANESSA" in r.getMessage() for r in records)

