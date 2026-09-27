"""Auth coverage for the whole /api surface.

- No credentials -> 401 on every route except the allow-list.
- Household session -> 403 on every admin-only route (admin/*, medications, pickups, activity, room delete).
- Every registered /api route declares exactly one role dependency.
"""

import pytest
from fastapi.routing import APIRoute

from app import auth
from app.main import PUBLIC_API_PATHS, SESSION_TOKEN_HEADER, app

# (method, path, json body) — one representative read and write per route family.
HOUSEHOLD_OK = [
    ("GET", "/api/session", None),
    ("GET", "/api/status", None),
    # rooms (all but delete)
    ("GET", "/api/rooms", None),
    ("GET", "/api/rooms/kitchen", None),
    ("POST", "/api/rooms", {"name": "X"}),
    ("PATCH", "/api/rooms/1", {"name": "X"}),
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
]

ADMIN_ONLY = [
    ("DELETE", "/api/rooms/1", None),
    ("GET", "/api/activity", None),
    # medications
    ("GET", "/api/medications", None),
    ("POST", "/api/medications", {"name": "X", "person": "X"}),
    ("PATCH", "/api/medications/1", {"name": "X"}),
    ("DELETE", "/api/medications/1", None),
    ("POST", "/api/medications/1/pickups", {"picked_up_on": "2026-01-01"}),
    ("GET", "/api/medications/1/pickups", None),
    ("DELETE", "/api/pickups/1", None),
    # admin
    ("POST", "/api/admin/password", {"current_password": "x", "new_password": "yyyyyyyy"}),
    ("PUT", "/api/admin/household-password", {"new_password": "yyyyyyyy"}),
    ("GET", "/api/admin/reminders", None),
    ("PUT", "/api/admin/reminders", {"reminder_email": ""}),
    ("POST", "/api/admin/reminders/test", None),
]

ALL_PROTECTED = HOUSEHOLD_OK + ADMIN_ONLY + [("GET", "/api/some/future/route", None)]


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


@pytest.mark.parametrize("method,path,body", ADMIN_ONLY, ids=_id)
def test_household_is_403_on_admin_routes(client, household_headers, method, path, body):
    r = client.request(method, path, json=body, headers=household_headers)
    assert r.status_code == 403, f"{method} {path} -> {r.status_code}"


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
                sample = route.path.replace("{room_id}", "1").replace("{med_id}", "1").replace("{pickup_id}", "1")
                assert (method, sample) in listed, f"admin route {method} {route.path} missing from ADMIN_ONLY"


def test_allow_list_is_open(client):
    assert client.get("/api/health").status_code == 200
    assert client.post("/api/login", json={"password": "wrong"}).status_code == 401
    r = client.post("/api/login", json={"password": "household-test-password"})
    assert r.status_code == 200 and r.json()["role"] == "household"
    r = client.post("/api/login", json={"password": "admin-test-password"})
    assert r.status_code == 200 and r.json()["role"] == "admin"


def test_admin_session_is_not_sliding(client, admin_headers):
    r = client.get("/api/rooms", headers=admin_headers)
    assert r.status_code == 200
    assert SESSION_TOKEN_HEADER not in r.headers


def test_household_renewed_token_is_valid_and_keeps_role(client, household_headers):
    renewed = client.get("/api/session", headers=household_headers).headers[SESSION_TOKEN_HEADER]
    r = client.get("/api/session", headers={"Authorization": f"Bearer {renewed}"})
    assert r.status_code == 200 and r.json()["role"] == "household"
    assert client.get("/api/medications", headers={"Authorization": f"Bearer {renewed}"}).status_code == 403


def test_token_ttls():
    assert auth.TOKEN_TTL[auth.ADMIN] <= 30 * 24 * 3600
    assert auth.TOKEN_TTL[auth.HOUSEHOLD] == 365 * 24 * 3600


def test_admin_read_and_write(client, admin_headers):
    assert client.get("/api/medications", headers=admin_headers).status_code == 200
    r = client.post("/api/upkeep", json={"name": "Test", "interval_days": 30}, headers=admin_headers)
    assert r.status_code == 201
    assert client.delete(f"/api/upkeep/{r.json()['id']}", headers=admin_headers).status_code == 204


def test_changing_household_password_logs_household_out_but_not_admin(client, admin_headers):
    hh = client.post("/api/login", json={"password": "household-test-password"}).json()["token"]
    hh_headers = {"Authorization": f"Bearer {hh}"}
    assert client.get("/api/session", headers=hh_headers).status_code == 200
    assert client.put("/api/admin/household-password", json={"new_password": "new-hh-pass"}, headers=admin_headers).status_code == 200
    assert client.get("/api/session", headers=hh_headers).status_code == 401
    assert client.get("/api/session", headers=admin_headers).status_code == 200
    assert client.post("/api/login", json={"password": "household-test-password"}).status_code == 401
    assert client.post("/api/login", json={"password": "new-hh-pass"}).json()["role"] == "household"
    # restore for other tests
    client.put("/api/admin/household-password", json={"new_password": "household-test-password"}, headers=admin_headers)


def test_page_urls_never_write(client):
    """NFC tags point at page URLs; a bare GET must never create a completion."""
    get_paths = {r.path for r in app.routes if isinstance(r, APIRoute) and "GET" in r.methods}
    assert not any("complete" in p for p in get_paths)
    for path in ("/", "/r/kitchen", "/upkeep"):
        assert client.get(path).status_code in (200, 404)  # SPA shell only; 404 when frontend isn't built


def test_env_password_replaces_legacy_default(monkeypatch):
    """A stored legacy default ('home') counts as unset and is replaced from the env var on restart."""
    auth.set_password(auth.HOUSEHOLD, auth.LEGACY_DEFAULT_PASSWORD[auth.HOUSEHOLD])
    monkeypatch.setenv("HOUSEHOLD_PASSWORD", "from-env")
    auth.ensure_admin_credentials()
    assert auth.check_password(auth.HOUSEHOLD, "from-env")
    # a password the admin chose is never overwritten by the env var
    auth.set_password(auth.HOUSEHOLD, "chosen-by-admin")
    auth.ensure_admin_credentials()
    assert auth.check_password(auth.HOUSEHOLD, "chosen-by-admin")
    auth.set_password(auth.HOUSEHOLD, "household-test-password")


@pytest.mark.parametrize("env_var", ["HOUSEHOLD_PASSWORD", "ADMIN_PASSWORD"])
def test_boot_fails_without_password_env_var(client, monkeypatch, env_var):
    """No stored password + no env var -> startup aborts naming the variable. Nothing is seeded."""
    from app.db import get_conn

    role = next(r for r, v in auth.PASSWORD_ENV.items() if v == env_var)
    with get_conn() as conn:
        stored = auth._get_setting(conn, auth.PASSWORD_KEY[role])
        conn.execute("DELETE FROM settings WHERE key = ?", (auth.PASSWORD_KEY[role],))
    monkeypatch.delenv(env_var, raising=False)
    try:
        with pytest.raises(auth.MissingPasswordError, match=env_var):
            auth.ensure_admin_credentials()
        with get_conn() as conn:
            assert auth._get_setting(conn, auth.PASSWORD_KEY[role]) is None
    finally:
        with get_conn() as conn:
            auth._set_setting(conn, auth.PASSWORD_KEY[role], stored)


def test_boot_fails_without_password_env_var_via_app(client, monkeypatch, tmp_path):
    """Full app startup (lifespan) refuses to run and logs the missing variable."""
    import logging

    from fastapi.testclient import TestClient

    from app import db

    monkeypatch.setattr(db, "DATA_DIR", tmp_path)
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "fresh.db")
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
    assert any(r.levelno == logging.CRITICAL and "HOUSEHOLD_PASSWORD" in r.getMessage() for r in records)

