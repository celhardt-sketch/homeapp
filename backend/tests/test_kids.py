"""Children's sizes and needs: REST (helpers have full read/write), staleness, duplicates, and the
eight MCP tools with fuzzy matching and disambiguation."""

import pytest
from fastmcp.exceptions import ToolError

from app.db import get_conn
from tests.test_mcp import _unwrap, mcp, server_url  # noqa: F401  (fixtures)


@pytest.fixture
def two_kids(client, admin_headers):
    henry = client.post("/api/children", json={"name": "Henry"}, headers=admin_headers).json()
    ava = client.post("/api/children", json={"name": "Ava"}, headers=admin_headers).json()
    yield henry, ava
    with get_conn() as conn:
        conn.execute("DELETE FROM shopping_items WHERE name LIKE '% for Henry%' OR name LIKE '% for Ava%'")
        conn.execute("DELETE FROM children WHERE id IN (?, ?)", (henry["id"], ava["id"]))


def test_helper_sets_size_and_it_is_attributed_with_age(client, susan_headers, two_kids):
    henry, _ = two_kids
    r = client.put(f"/api/children/{henry['id']}/sizes", json={"category": "shoes", "value": "13 toddler"}, headers=susan_headers)
    assert r.status_code == 200, r.text
    s = r.json()
    assert s["updated_by"] == "Susan" and s["age_days"] == 0 and s["stale"] is False and s["category"] == "Shoes"

    # replacing the same category (plural/case folded) keeps one row
    client.put(f"/api/children/{henry['id']}/sizes", json={"category": "Shoe", "value": "1 youth"}, headers=susan_headers)
    sizes = client.get(f"/api/children/{henry['id']}", headers=susan_headers).json()["sizes"]
    assert [(x["category"], x["value"]) for x in sizes] == [("Shoes", "1 youth")]

    # an old value is flagged stale with its age
    with get_conn() as conn:
        conn.execute("UPDATE child_sizes SET updated_at = '2020-01-01T00:00:00+00:00' WHERE child_id = ?", (henry["id"],))
    s = client.get("/api/sizes?category=shoes", headers=susan_headers).json()[0]
    assert s["stale"] is True and s["age_days"] > 180


def test_need_duplicate_shopping_and_mark_have(client, susan_headers, vanessa_headers, two_kids):
    _, ava = two_kids
    body = {"child_id": ava["id"], "category": "winter coat", "season": "winter 2026", "add_to_shopping": True}
    r = client.post("/api/needs", json=body, headers=susan_headers)
    assert r.status_code == 201, r.text
    need = r.json()
    assert need["duplicate"] is False and need["created_by"] == "Susan" and need["shopping_item_id"]
    shop = client.get("/api/shopping-items", headers=susan_headers).json()
    assert any(s["id"] == need["shopping_item_id"] and "Ava" in s["name"] for s in shop)

    r = client.post("/api/needs", json=body, headers=vanessa_headers)
    assert r.status_code == 200 and r.json()["duplicate"] is True and r.json()["id"] == need["id"]
    assert len(client.get(f"/api/needs?child_id={ava['id']}", headers=susan_headers).json()) == 1

    r = client.patch(f"/api/needs/{need['id']}", json={"status": "have"}, headers=vanessa_headers)
    assert r.json()["status"] == "have" and r.json()["resolved_by"] == "Vanessa"
    assert client.get(f"/api/needs?child_id={ava['id']}", headers=susan_headers).json() == []
    bought = next(s for s in client.get("/api/shopping-items?include_done=true", headers=susan_headers).json() if s["id"] == need["shopping_item_id"])
    assert bought["done"] and bought["bought_by"] == "Vanessa"


def test_mcp_children_tools(mcp, client, admin_headers, two_kids):
    henry, ava = two_kids
    kids = _unwrap(mcp("list_children"))
    assert {k["name"] for k in kids} >= {"Henry", "Ava"}

    r = mcp("set_size", child="henry", category="shoes", value="13 toddler", idempotency_key="k1")
    assert r["saved"] and r["size"]["child"] == "Henry" and r["size"]["updated_by"] == "Claude"
    assert mcp("set_size", child="henry", category="shoes", value="99", idempotency_key="k1")["replayed"]
    assert mcp("get_child", child="Henri")["sizes"][0]["value"] == "13 toddler"
    mcp("set_size", child="ava", category="Shoe", value="9 toddler")
    assert len(mcp("list_sizes", category="shoes")["sizes"]) == 2

    with pytest.raises(ToolError, match="No child called 'Zed'.*Henry"):
        mcp("get_child", child="Zed")
    with pytest.raises(ToolError, match="No category called 'hats'"):
        mcp("list_sizes", category="hats")

    # needs, batch season check, duplicate, have
    check = mcp("seasonal_check", categories=["winter coat", "snow boots"], season="winter 2026")
    assert check["gaps"] >= 4 and all(r["status"] == "unknown" for r in check["rows"] if r["child"] in ("Henry", "Ava"))
    r = mcp("add_need", child="ava", category="winter coat", season="winter 2026", add_to_shopping=True)
    assert r["created"] and r["need"]["on_shopping_list"]
    r = mcp("add_need", child="Ava", category="Winter Coats", season="winter 2026")
    assert r["duplicate"] and not r["created"]
    assert len(mcp("list_needs", child="ava")["needs"]) == 1
    rows = {(r["child"], r["category"]): r["status"] for r in mcp("seasonal_check", categories=["coat"], season="winter 2026")["rows"]}
    assert rows[("Ava", "Winter coat")] == "needed"

    r = mcp("mark_have", child="ava", category="coat")
    assert r["handled"] and r["need"]["resolved_by"] == "Claude"
    assert mcp("list_needs", child="ava")["needs"] == []
    assert mcp("list_needs", child="ava", status="all")["needs"][0]["status"] == "have"
    with pytest.raises(ToolError, match="already marked handled"):
        mcp("mark_have", child="ava", category="coat")

    # a child with two open needs in categories that both match "boots" -> ask, change nothing
    mcp("add_need", child="henry", category="snow boots")
    mcp("add_need", child="henry", category="rain boots")
    r = mcp("mark_have", child="henry", category="boots")
    assert r["needs_disambiguation"] and len(r["candidates"]) == 2
    assert len(mcp("list_needs", child="henry")["needs"]) == 2
