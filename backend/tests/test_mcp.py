"""The eight MCP acceptance tests, run over real Streamable HTTP against a uvicorn server
in this process, authenticated the way Claude will be: OAuth (see conftest.oauth_connect).
"""

import asyncio
import socket
import threading
import time
from datetime import date, timedelta

import httpx
import pytest
import uvicorn
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport
from fastmcp.exceptions import ToolError

from app import oauth
from app.main import app
from tests.conftest import oauth_connect

TOOLS = {
    "get_home_status", "list_rooms", "list_tasks", "add_task", "complete_task", "list_upkeep",
    "log_upkeep_done", "list_pantry", "add_pantry_items", "update_pantry_item", "remove_pantry_item",
    "list_shopping", "add_to_shopping", "find",
}


@pytest.fixture(scope="module")
def server_url(client):
    """uvicorn on a free port, sharing the test DB. `client` keeps the app lifespan alive."""
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", lifespan="off")
    server = uvicorn.Server(config)
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(100):
        try:
            httpx.get(f"http://127.0.0.1:{port}/api/health")
            break
        except httpx.HTTPError:
            time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True


@pytest.fixture
def mcp(server_url, client):
    token = oauth_connect(client)["access_token"]

    def call(tool: str, **args):
        async def go():
            async with Client(StreamableHttpTransport(f"{server_url}/mcp", headers={"Authorization": f"Bearer {token}"})) as c:
                res = await c.call_tool(tool, args, raise_on_error=False)
                if res.is_error:
                    raise ToolError(res.content[0].text)
                return res.structured_content if res.structured_content is not None else res.data

        return asyncio.run(go())

    return call


def _unwrap(result):
    # fastmcp wraps non-object results as {"result": ...}
    return result["result"] if isinstance(result, dict) and set(result) == {"result"} else result


# 1. handshake + 14 tools
def test_handshake_lists_all_14_tools(server_url, client):
    token = oauth_connect(client)["access_token"]

    async def go():
        async with Client(StreamableHttpTransport(f"{server_url}/mcp", headers={"Authorization": f"Bearer {token}"})) as c:
            return {t.name: t.description for t in await c.list_tools()}

    tools = asyncio.run(go())
    assert set(tools) == TOOLS
    assert all(tools[n] for n in TOOLS), "every tool needs a spoken-language description"


def test_mcp_requires_oauth_token(server_url):
    r = httpx.post(f"{server_url}/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                   headers={"Accept": "application/json, text/event-stream", "Content-Type": "application/json"})
    assert r.status_code == 401
    assert "resource_metadata" in r.headers.get("www-authenticate", "")
    meta = httpx.get(f"{server_url}/.well-known/oauth-protected-resource/mcp").json()
    assert meta["resource"].endswith("/mcp")


def test_household_password_token_is_not_accepted_on_mcp(server_url, client, household_headers):
    r = httpx.post(f"{server_url}/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                   headers={**household_headers, "Accept": "application/json, text/event-stream"})
    assert r.status_code == 401


def test_wrong_admin_password_denies_consent(client):
    assert oauth_connect(client, admin_password="nope") == {"error": "consent_failed", "status": 401}


# 3. revocation leaves household + admin alone
def test_revoke_connector_keeps_household_and_admin_sessions(client, admin_headers, household_headers):
    tok = oauth_connect(client)
    conn_headers = {"Authorization": f"Bearer {tok['access_token']}"}
    assert client.get("/api/rooms", headers=conn_headers).status_code == 200
    status = client.get("/api/admin/connector", headers=admin_headers).json()
    assert status["connected"] and status["clients"]
    r = client.delete("/api/admin/connector", headers=admin_headers)
    assert r.status_code == 200 and r.json()["revoked_tokens"] >= 2
    assert client.get("/api/rooms", headers=conn_headers).status_code == 401
    assert client.post("/token", data={"grant_type": "refresh_token", "refresh_token": tok["refresh_token"],
                                       "client_id": tok["client_id"]}).status_code in (400, 401)
    assert client.get("/api/rooms", headers=household_headers).status_code == 200
    assert client.get("/api/medications", headers=admin_headers).status_code == 200
    assert client.get("/api/admin/connector", headers=admin_headers).json()["connected"] is False


def test_refresh_token_rotates(client):
    tok = oauth_connect(client)
    r = client.post("/token", data={"grant_type": "refresh_token", "refresh_token": tok["refresh_token"],
                                    "client_id": tok["client_id"]})
    assert r.status_code == 200
    new = r.json()
    assert new["access_token"] != tok["access_token"]
    assert oauth.access_token_valid(new["access_token"])
    assert not oauth.access_token_valid(tok["access_token"])


# 4. disambiguation
def test_update_pantry_rice_is_ambiguous_and_changes_nothing(mcp, client, household_headers):
    ids = [
        client.post("/api/pantry", json={"name": name, "quantity": "2 bags", "category": "Pantry"}, headers=household_headers).json()["id"]
        for name in ("Jasmine rice", "Brown rice")
    ]
    before = {p["name"]: p["quantity"] for p in client.get("/api/pantry", headers=household_headers).json()}
    res = mcp("update_pantry_item", item="rice", quantity="half a bag")
    assert res["needs_disambiguation"] is True
    assert {c["name"] for c in res["candidates"]} == {"Jasmine rice", "Brown rice"}
    assert all("quantity" in c and "location" in c for c in res["candidates"])
    after = {p["name"]: p["quantity"] for p in client.get("/api/pantry", headers=household_headers).json()}
    assert after == before
    res = mcp("update_pantry_item", item="jasmin rice", quantity="half a bag")
    assert res["updated"] and res["item"]["quantity"] == "half a bag"
    for i in ids:  # other test files insert plain "Rice"
        client.delete(f"/api/pantry/{i}", headers=household_headers)


def test_no_match_lists_rooms(mcp, client, household_headers):
    names = [r["name"] for r in client.get("/api/rooms", headers=household_headers).json()]
    with pytest.raises(ToolError) as e:
        mcp("add_task", title="Sweep", room="attic")
    msg = str(e.value)
    assert msg == f"No room called 'attic'. Rooms are: {', '.join(names)}."
    assert "Traceback" not in msg


# 5. duplicate flag
def test_add_pantry_duplicate_inserts_nothing(mcp, client, household_headers):
    client.post("/api/pantry", json={"name": "Peanut butter", "quantity": "1"}, headers=household_headers)
    count = len(client.get("/api/pantry", headers=household_headers).json())
    res = mcp("add_pantry_items", items=[{"name": "peanut butters", "quantity": "2"}])
    assert res["added"] == []
    assert len(res["duplicates"]) == 1 and res["duplicates"][0]["duplicate"] is True
    assert res["duplicates"][0]["name"] == "Peanut butter"
    assert len(client.get("/api/pantry", headers=household_headers).json()) == count


# 6. bulk insert + idempotency
def test_add_15_pantry_items_idempotent(mcp, client, household_headers):
    names = ["Cumin", "Turmeric", "Coriander", "Paprika", "Cinnamon", "Nutmeg", "Cloves", "Cardamom",
             "Oregano", "Thyme", "Rosemary", "Basil", "Bay leaves", "Sage", "Dill"]
    count = len(client.get("/api/pantry", headers=household_headers).json())
    items = [{"name": n, "quantity": "1 jar", "location": "Spice rack"} for n in names]
    res = mcp("add_pantry_items", items=items, idempotency_key="unpack-1")
    assert len(res["added"]) == 15 and res["duplicates"] == []
    assert len(client.get("/api/pantry", headers=household_headers).json()) == count + 15
    again = mcp("add_pantry_items", items=items, idempotency_key="unpack-1")
    assert again["replayed"] is True and len(again["added"]) == 15
    assert len(client.get("/api/pantry", headers=household_headers).json()) == count + 15


def test_burst_of_twenty_writes_is_not_rate_limited(mcp):
    for i in range(20):
        assert mcp("add_to_shopping", items=[f"burst thing {i:02d}"])["added"]


# 7. recurring task completion schedules the next date
def test_complete_recurring_task_schedules_next(mcp, client, household_headers):
    room = client.post("/api/rooms", json={"name": "Scullery"}, headers=household_headers).json()
    r = client.post("/api/tasks", json={"room_id": room["id"], "title": "Descale the kettle", "frequency_days": 14}, headers=household_headers)
    assert r.status_code == 201
    res = mcp("complete_task", task="descale kettle", done_by="Courtney", note="need more descaler")
    assert res["completed"] is True
    assert res["task"]["last_completed_by"] == "Courtney"
    assert res["task"]["status"] == "ok"
    assert res["next_due_on"] == (date.today() + timedelta(days=14)).isoformat()
    shopping = mcp("list_shopping")
    assert any(i["item"] == "need more descaler" for i in shopping["to_buy_for_tasks"])
    tasks = _unwrap(mcp("list_tasks", room="scullery", status="ok"))
    assert any(t["title"] == "Descale the kettle" for t in tasks)


def test_add_task_with_recurrence_and_due_date(mcp):
    due = (date.today() + timedelta(days=3)).isoformat()
    res = mcp("add_task", title="Wash the curtains", room="the bathroom", recurrence="every 3 months", due_on=due)
    assert res["task"]["frequency_days"] == 90
    assert res["task"]["due_on"] == due
    assert res["task"]["room"] == "Bathroom"


# 8. upkeep with interval 180
def test_log_upkeep_done_180_days(mcp, client, household_headers):
    heater = next(i for i in client.get("/api/upkeep", headers=household_headers).json() if "water heater" in i["name"].lower())
    assert client.patch(f"/api/upkeep/{heater['id']}", json={"interval_days": 180}, headers=household_headers).status_code == 200
    done = date.today() - timedelta(days=10)
    res = mcp("log_upkeep_done", item="flushed the water heater", done_on=done.isoformat(), note="drained fully")
    item = res["item"]
    assert item["last_done_on"] == done.isoformat()
    assert item["due_on"] == (done + timedelta(days=180)).isoformat()
    assert item["days_left"] == 170
    assert item["status"] == "ok"


def test_never_logged_upkeep_is_due(mcp, client, household_headers):
    r = client.post(
        "/api/upkeep",
        json={"name": "Change furnace filter zz", "category": "HVAC", "interval_days": 90},
        headers=household_headers,
    )
    assert r.status_code == 201
    item = r.json()
    try:
        assert item["last_done_on"] is None
        assert item["status"] == "due"
        assert item["due_on"] == date.today().isoformat()
        assert item["days_left"] == 0

        due = _unwrap(mcp("list_upkeep", due_only=True))
        assert item["id"] in [i["id"] for i in due]

        status = client.get("/api/status", headers=household_headers).json()
        assert item["id"] in [i["id"] for i in status["upkeep_due"]]
    finally:
        client.delete(f"/api/upkeep/{item['id']}", headers=household_headers)


def test_quantity_delta_and_find(mcp):
    added = mcp("add_pantry_items", items=[{"name": "Large eggs", "quantity": "12", "location": "Fridge"}])
    assert len(added["added"]) == 1
    res = mcp("update_pantry_item", item="large egg", quantity_delta=-2)
    assert res["item"]["quantity"] == "10"
    found = mcp("find", query="egg")
    assert any(p["name"] == "Large eggs" for p in found["pantry"])
    status = mcp("get_home_status")
    assert "medications" not in str(status).lower()
