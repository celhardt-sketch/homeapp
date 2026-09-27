import os
import tempfile

# app.db reads DATA_DIR at import time, so set it before anything imports the app.
os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="hm-test-")
os.environ["ADMIN_PASSWORD"] = "admin-test-password"
os.environ["HOUSEHOLD_PASSWORD"] = "household-test-password"

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


@pytest.fixture(scope="session")
def household_headers(client):
    return {"Authorization": f"Bearer {_login(client, 'household-test-password')['token']}"}
