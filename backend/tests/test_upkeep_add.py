"""Adding upkeep jobs: duplicate check on POST /api/upkeep and the MCP add_upkeep_items tool,
which takes frequency in words (weekly, monthly, every 6 months...) or days."""

import pytest
from fastmcp.exceptions import ToolError

from app.mcp_server import _frequency_to_days
from tests.test_mcp import mcp, server_url  # noqa: F401  (fixtures)


@pytest.mark.parametrize(
    "text,days",
    [("daily", 1), ("Weekly", 7), ("monthly", 30), ("quarterly", 90), ("every 6 months", 180), ("yearly", 365),
     ("annually", 365), ("45 days", 45), ("every 2 weeks", 14), ("3 years", 1095), (60, 60), ("60", 60)],
)
def test_frequency_words(text, days):
    assert _frequency_to_days(text) == days


def test_frequency_gibberish_is_a_plain_error():
    with pytest.raises(ToolError, match="don't understand the frequency 'sometimes'"):
        _frequency_to_days("sometimes")


def test_api_upkeep_duplicate_returns_existing(client, susan_headers):
    a = client.post("/api/upkeep", json={"name": "Clean dryer vent", "interval_days": 180}, headers=susan_headers).json()
    assert a["duplicate"] is False
    b = client.post("/api/upkeep", json={"name": "clean  dryer vents", "interval_days": 30}, headers=susan_headers).json()
    assert b["duplicate"] is True and b["id"] == a["id"] and b["interval_days"] == 180
    client.delete(f"/api/upkeep/{a['id']}", headers=susan_headers)


def test_mcp_add_upkeep_items_batch_frequency_and_replay(mcp, client, admin_headers):
    r = mcp(
        "add_upkeep_items",
        items=[
            {"name": "Check sump pump", "frequency": "monthly", "category": "Basement"},
            {"name": "Descale espresso machine", "frequency": "yearly", "last_done_on": "2026-01-15"},
            {"name": "Rotate mattress", "frequency": "every 6 months"},
        ],
        idempotency_key="upkeep-1",
    )
    assert r["duplicates"] == [] and [i["interval_days"] for i in r["added"]] == [30, 365, 180]
    assert r["added"][1]["last_done_on"] == "2026-01-15"
    assert r["added"][0]["status"] == "due"  # never logged counts as due

    again = mcp("add_upkeep_items", items=[{"name": "Check sump pump", "frequency": "monthly"}], idempotency_key="upkeep-1")
    assert again.pop("replayed") is True and again == r

    dup = mcp("add_upkeep_items", items=[{"name": "check the sump pump", "frequency": "weekly"}])
    assert dup["added"] == [] and dup["duplicates"][0]["id"] == r["added"][0]["id"]

    with pytest.raises(ToolError, match="frequency 'whenever'"):
        mcp("add_upkeep_items", items=[{"name": "Oil hinges", "frequency": "whenever"}])

    for i in r["added"]:
        client.delete(f"/api/upkeep/{i['id']}", headers=admin_headers)
