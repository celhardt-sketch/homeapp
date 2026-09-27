import os
import tempfile

# app.db reads DATA_DIR at import time, so set it before anything imports the app.
os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="hm-test-")
os.environ["ADMIN_PASSWORD"] = "test-password"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402


@pytest.fixture(scope="session")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="session")
def auth_headers(client):
    r = client.post("/api/admin/login", json={"password": "test-password"})
    assert r.status_code == 200
    return {"Authorization": f"Bearer {r.json()['token']}"}
