"""Pantry par levels, bulk insert, duplicate detection, and the /api/status summary."""

from datetime import date, timedelta

import pytest

from app.main import app
from fastapi.routing import APIRoute

MED_WORDS = ("medic", "pickup", "prescription", "reorder", "picked_up", "person")


@pytest.fixture(autouse=True)
def clean_pantry(client, admin_headers):
    yield
    for item in client.get("/api/pantry", headers=admin_headers).json():
        client.delete(f"/api/pantry/{item['id']}", headers=admin_headers)


def _keys(obj, out: set[str]) -> set[str]:
    if isinstance(obj, dict):
        for k, v in obj.items():
            out.add(k)
            _keys(v, out)
    elif isinstance(obj, list):
        for v in obj:
            _keys(v, out)
    return out


# ---------- par_level ----------


def test_par_level_roundtrip_and_low(client, household_headers):
    r = client.post("/api/pantry", json={"name": "Rice", "quantity": "1 bag", "par_level": 2}, headers=household_headers)
    assert r.status_code == 201
    item = r.json()
    assert item["par_level"] == 2 and item["below_par"] is True and item["low"] is True and item["duplicate"] is False

    r = client.patch(f"/api/pantry/{item['id']}", json={"quantity": "5 bags"}, headers=household_headers)
    assert r.json()["below_par"] is False and r.json()["low"] is False

    r = client.patch(f"/api/pantry/{item['id']}", json={"quantity": "0", "par_level": None}, headers=household_headers)
    assert r.json()["par_level"] is None and r.json()["below_par"] is False, "null par_level means never low"

    listed = client.get("/api/pantry", headers=household_headers).json()
    assert {"par_level", "below_par", "expires_on", "days_to_expiry"} <= set(listed[0])


def test_par_level_without_numeric_quantity_is_not_low(client, household_headers):
    r = client.post("/api/pantry", json={"name": "Flour", "quantity": "some", "par_level": 1}, headers=household_headers)
    assert r.json()["below_par"] is False


# ---------- bulk insert ----------


def test_array_insert_creates_all_rows_in_one_call(client, household_headers):
    names = [
        "Eggs", "Whole milk", "Cheddar", "Bananas", "Apples", "Chicken thighs", "Ground beef", "Spinach",
        "Carrots", "Yogurt", "Pasta", "Tomato sauce", "Bread", "Orange juice", "Paper towels",
    ]
    assert len(names) == 15
    items = [{"name": n, "quantity": str(i + 1)} for i, n in enumerate(names)]
    r = client.post("/api/pantry", json=items, headers=household_headers)
    assert r.status_code == 201
    created = r.json()
    assert isinstance(created, list) and len(created) == 15
    assert all(c["duplicate"] is False for c in created)
    assert len({c["id"] for c in created}) == 15
    names = {p["name"] for p in client.get("/api/pantry", headers=household_headers).json()}
    assert all(i["name"] in names for i in items)


def test_single_object_insert_still_returns_object(client, household_headers):
    r = client.post("/api/pantry", json={"name": "Single object"}, headers=household_headers)
    assert r.status_code == 201 and isinstance(r.json(), dict)


def test_empty_array_is_rejected(client, household_headers):
    assert client.post("/api/pantry", json=[], headers=household_headers).status_code == 422


# ---------- duplicate detection ----------


@pytest.mark.parametrize(
    "existing,attempt",
    [
        ("Olive Oil", "olive oil"),  # case-insensitive
        ("Olive Oil", "Extra virgin olive oil"),  # substring
        ("Peanut Butter", "Peanut Buter"),  # fuzzy typo
    ],
)
def test_near_duplicate_returns_existing_and_inserts_nothing(client, household_headers, existing, attempt):
    first = client.post("/api/pantry", json={"name": existing}, headers=household_headers).json()
    before = len(client.get("/api/pantry", headers=household_headers).json())

    r = client.post("/api/pantry", json={"name": attempt}, headers=household_headers)
    assert r.status_code == 201
    body = r.json()
    assert body["duplicate"] is True
    assert body["id"] == first["id"] and body["name"] == existing
    assert body["requested_name"] == attempt
    assert len(client.get("/api/pantry", headers=household_headers).json()) == before


@pytest.mark.parametrize("a,b", [("Black beans", "Coffee filters"), ("Size 4 diapers", "Size 5 diapers"), ("Tomato sauce", "Tomato paste")])
def test_distinct_names_are_not_flagged(client, household_headers, a, b):
    client.post("/api/pantry", json={"name": a}, headers=household_headers)
    r = client.post("/api/pantry", json={"name": b}, headers=household_headers)
    assert r.json()["duplicate"] is False


def test_duplicates_inside_one_batch_collapse(client, household_headers):
    r = client.post("/api/pantry", json=[{"name": "Oat milk"}, {"name": "oat milk"}], headers=household_headers)
    created = r.json()
    assert [c["duplicate"] for c in created] == [False, True]
    assert created[0]["id"] == created[1]["id"]


# ---------- /api/status ----------


def test_status_shape(client, household_headers):
    r = client.get("/api/status", headers=household_headers)
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {
        "generated_on",
        "tasks_overdue",
        "tasks_due_today",
        "tasks_due_within_7_days",
        "upkeep_due",
        "pantry_below_par",
        "pantry_expiring_within_7_days",
    }
    assert all(isinstance(body[k], list) for k in body if k != "generated_on")


def test_status_reports_pantry_par_and_expiry(client, household_headers):
    soon = (date.today() + timedelta(days=3)).isoformat()
    later = (date.today() + timedelta(days=30)).isoformat()
    client.post(
        "/api/pantry",
        json=[
            {"name": "Status low", "quantity": "1", "par_level": 3},
            {"name": "Status expiring", "quantity": "9", "expires_on": soon},
            {"name": "Status fine", "quantity": "9", "par_level": 1, "expires_on": later},
        ],
        headers=household_headers,
    )
    body = client.get("/api/status", headers=household_headers).json()
    assert [p["name"] for p in body["pantry_below_par"]] == ["Status low"]
    assert [p["name"] for p in body["pantry_expiring_within_7_days"]] == ["Status expiring"]


def test_status_reports_due_tasks_and_upkeep(client, household_headers, admin_headers):
    room = client.post("/api/rooms", json={"name": "Status room"}, headers=admin_headers).json()
    task = client.post(
        "/api/tasks", json={"room_id": room["id"], "title": "Never done", "frequency_days": 7}, headers=household_headers
    ).json()
    body = client.get("/api/status", headers=household_headers).json()
    assert any(t["id"] == task["id"] and t["room_slug"] == room["slug"] for t in body["tasks_due_today"])

    up = client.post("/api/upkeep", json={"name": "Status upkeep", "interval_days": 30}, headers=household_headers).json()
    client.post(
        "/api/upkeep/%d/logs" % up["id"],
        json={"done_on": (date.today() - timedelta(days=45)).isoformat()},
        headers=household_headers,
    )
    body = client.get("/api/status", headers=household_headers).json()
    assert any(u["id"] == up["id"] for u in body["upkeep_due"])
    client.delete(f"/api/upkeep/{up['id']}", headers=household_headers)
    client.delete(f"/api/tasks/{task['id']}", headers=household_headers)


@pytest.mark.parametrize("who", ["household_headers", "admin_headers"])
def test_status_never_contains_medication_data(client, admin_headers, request, who):
    """Summary endpoints are an easy way to leak past a role boundary: assert no med fields for any role."""
    med = client.post(
        "/api/medications", json={"name": "Zestril-secret", "person": "Grandma-secret"}, headers=admin_headers
    ).json()
    client.post(
        "/api/medications/%d/pickups" % med["id"],
        json={"picked_up_on": (date.today() - timedelta(days=40)).isoformat()},
        headers=admin_headers,
    )  # overdue reorder, so it would show up if the endpoint summarised meds
    try:
        r = client.get("/api/status", headers=request.getfixturevalue(who))
        assert r.status_code == 200
        keys = _keys(r.json(), set())
        assert not any(w in k.lower() for k in keys for w in MED_WORDS), keys
        text = r.text.lower()
        assert "zestril" not in text and "grandma" not in text
        assert "medication" not in text and "pickup" not in text
    finally:
        client.delete(f"/api/medications/{med['id']}", headers=admin_headers)


def test_status_route_is_household_readable():
    route = next(r for r in app.routes if isinstance(r, APIRoute) and r.path == "/api/status")
    assert route.methods == {"GET"}
