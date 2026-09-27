import base64
import hashlib
import os
import secrets
import tempfile
from urllib.parse import parse_qs, urlparse

# app.db reads DATA_DIR at import time, so set it before anything imports the app.
os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="hm-test-")
os.environ["ADMIN_PASSWORD"] = "admin-test-password"
os.environ["HOUSEHOLD_PASSWORD"] = "household-test-password"
os.environ["PUBLIC_URL"] = "http://localhost"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402


@pytest.fixture(scope="session")
def client():
    with TestClient(app) as c:
        yield c


def _login(client, password):
    r = client.post("/api/login", json={"password": password})
    assert r.status_code == 200
    return r.json()


@pytest.fixture(scope="session")
def admin_headers(client):
    return {"Authorization": f"Bearer {_login(client, 'admin-test-password')['token']}"}


@pytest.fixture
def household_headers(client):
    # function-scoped: a password-change test bumps the household token version
    return {"Authorization": f"Bearer {_login(client, 'household-test-password')['token']}"}


def oauth_connect(client, admin_password="admin-test-password", client_name="Test Claude") -> dict:
    """Full OAuth 2.1 flow the way an MCP client does it: discovery -> dynamic registration ->
    PKCE authorize -> consent page (admin password) -> code exchange. Returns the token response."""
    redirect_uri = "http://localhost:9999/callback"
    meta = client.get("/.well-known/oauth-authorization-server").json()
    reg = client.post(
        meta["registration_endpoint"],
        json={"client_name": client_name, "redirect_uris": [redirect_uri], "token_endpoint_auth_method": "none"},
    )
    assert reg.status_code == 201, reg.text
    client_id = reg.json()["client_id"]
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    r = client.get(
        meta["authorization_endpoint"],
        params={
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "state": "xyz",
            "scope": "home",
        },
        follow_redirects=False,
    )
    assert r.status_code in (302, 307), r.text
    approve_url = r.headers["location"]
    assert "/connect/approve?req=" in approve_url
    req = parse_qs(urlparse(approve_url).query)["req"][0]
    assert client.get(approve_url).status_code == 200
    r = client.post("/connect/approve", data={"req": req, "password": admin_password}, follow_redirects=False)
    if r.status_code != 303:
        return {"error": "consent_failed", "status": r.status_code}
    cb = urlparse(r.headers["location"])
    assert cb.netloc == "localhost:9999"
    q = parse_qs(cb.query)
    if "error" in q:
        return {"error": q["error"][0]}
    assert q["state"] == ["xyz"]
    tok = client.post(
        meta["token_endpoint"],
        data={
            "grant_type": "authorization_code",
            "code": q["code"][0],
            "redirect_uri": redirect_uri,
            "client_id": client_id,
            "code_verifier": verifier,
        },
    )
    assert tok.status_code == 200, tok.text
    return {**tok.json(), "client_id": client_id}


@pytest.fixture
def connector_token(client):
    return oauth_connect(client)["access_token"]


@pytest.fixture
def connector_headers(connector_token):
    return {"Authorization": f"Bearer {connector_token}"}
