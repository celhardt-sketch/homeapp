"""Prescription refill tracking: derived status, called_waiting, persistence, reminders, access, MCP."""

from datetime import date, timedelta

import pytest
from fastmcp.exceptions import ToolError

from app import mcp_server, refills
from app.db import get_conn


def days_ago(n: int) -> str:
    return (date.today() - timedelta(days=n)).isoformat()


@pytest.fixture
def child(client, admin_headers):
    c = client.post("/api/children", json={"name": "Henry"}, headers=admin_headers).json()
    yield c
    with get_conn() as conn:
        conn.execute("DELETE FROM notifications WHERE kind = 'refill'")
        conn.execute("DELETE FROM children WHERE id = ?", (c["id"],))


def make_rx(client, admin_headers, child, name="Focalin", pickup_days_ago=None, **extra):
    rx = client.post(
        "/api/prescriptions", json={"child_id": child["id"], "name": name, **extra}, headers=admin_headers
    ).json()
    if pickup_days_ago is not None:
        r = client.post(
            f"/api/prescriptions/{rx['id']}/pickups", json={"picked_up_on": days_ago(pickup_days_ago)}, headers=admin_headers
        )
        assert r.status_code == 201, r.text
    return rx


def read_rx(client, headers, rx_id) -> dict:
    return next(p for p in client.get("/api/prescriptions", headers=headers).json() if p["id"] == rx_id)


def due_ids(client, headers) -> set[int]:
    return {p["id"] for p in client.get("/api/refills", headers=headers).json()}


def set_called_on(rx_id: int, when: str) -> None:
    with get_conn() as conn:
        conn.execute("UPDATE prescriptions SET called_on = ? WHERE id = ?", (when, rx_id))


# ---------- derived status ----------


def test_no_pickup_yet(client, admin_headers, child):
    rx = make_rx(client, admin_headers, child)
    assert rx["refill_status"] == "no_pickup" and rx["days_since_pickup"] is None and rx["days_of_supply_left"] is None
    assert rx["days_supply"] == 30 and rx["refill_after_days"] == 28
    assert rx["id"] not in due_ids(client, admin_headers)


def test_ok_before_day_28(client, admin_headers, child):
    rx = make_rx(client, admin_headers, child, pickup_days_ago=27)
    p = read_rx(client, admin_headers, rx["id"])
    assert p["refill_status"] == "ok" and p["days_since_pickup"] == 27 and p["days_of_supply_left"] == 3
    assert rx["id"] not in due_ids(client, admin_headers)


def test_refill_due_at_28_days_and_urgent_at_30(client, admin_headers, child):
    a = make_rx(client, admin_headers, child, name="Focalin", pickup_days_ago=28)
    b = make_rx(client, admin_headers, child, name="Zyrtec", pickup_days_ago=30)
    pa, pb = read_rx(client, admin_headers, a["id"]), read_rx(client, admin_headers, b["id"])
    assert pa["refill_status"] == "refill_due" and pa["days_since_pickup"] == 28 and pa["days_of_supply_left"] == 2
    assert pb["refill_status"] == "urgent" and pb["days_since_pickup"] == 30 and pb["days_of_supply_left"] == 0
    assert due_ids(client, admin_headers) >= {a["id"], b["id"]}
    due = client.get("/api/refills", headers=admin_headers).json()
    assert [p["id"] for p in due if p["id"] in (a["id"], b["id"])] == [b["id"], a["id"]], "most urgent first"


def test_refill_due_persists_days_later_without_pickup(client, admin_headers, child):
    """Day 33 with a 40-day supply: still refill_due, still listed. Nothing clears it but a pickup."""
    rx = make_rx(client, admin_headers, child, pickup_days_ago=33, days_supply=40)
    assert read_rx(client, admin_headers, rx["id"])["refill_status"] == "refill_due"
    assert rx["id"] in due_ids(client, admin_headers)
    refills.create_due_notifications()  # a reminder run doesn't "consume" it
    assert rx["id"] in due_ids(client, admin_headers)
    assert rx["id"] in {e["prescription_id"] for e in client.get("/api/status", headers=admin_headers).json()["refills"]}


# ---------- called_waiting ----------


def test_mark_called_quiets_refill_due_for_two_days_then_it_returns(client, admin_headers, susan_headers, child):
    rx = make_rx(client, admin_headers, child, pickup_days_ago=28, days_supply=40)
    r = client.post(f"/api/prescriptions/{rx['id']}/called", json={"notes": "ready Thursday"}, headers=susan_headers)
    assert r.status_code == 200
    p = r.json()
    assert p["refill_status"] == "called_waiting" and p["called_by"] == "Susan" and p["called_notes"] == "ready Thursday"
    assert rx["id"] not in due_ids(client, admin_headers)
    assert refills.create_due_notifications() == 0 or rx["id"] not in {
        n["ref_id"] for n in client.get("/api/me/notifications", headers=admin_headers).json() if n["kind"] == "refill"
    }

    set_called_on(rx["id"], days_ago(1))  # yesterday: still quiet
    assert read_rx(client, admin_headers, rx["id"])["refill_status"] == "called_waiting"

    set_called_on(rx["id"], days_ago(2))  # two days on: back on the list
    p = read_rx(client, admin_headers, rx["id"])
    assert p["refill_status"] == "refill_due" and p["called_on"] is None
    assert rx["id"] in due_ids(client, admin_headers)


def test_mark_called_does_not_suppress_urgent(client, admin_headers, child):
    rx = make_rx(client, admin_headers, child, pickup_days_ago=31)
    p = client.post(f"/api/prescriptions/{rx['id']}/called", json={}, headers=admin_headers).json()
    assert p["refill_status"] == "urgent"
    assert rx["id"] in due_ids(client, admin_headers)


def test_pickup_clears_due_state_and_called_waiting(client, admin_headers, vanessa_headers, child):
    rx = make_rx(client, admin_headers, child, pickup_days_ago=29, days_supply=40)
    client.post(f"/api/prescriptions/{rx['id']}/called", json={"notes": "called"}, headers=admin_headers)
    assert read_rx(client, admin_headers, rx["id"])["refill_status"] == "called_waiting"

    r = client.post(f"/api/prescriptions/{rx['id']}/pickups", json={"notes": "30 tablets"}, headers=vanessa_headers)
    assert r.status_code == 201
    p = r.json()
    assert p["refill_status"] == "ok" and p["days_since_pickup"] == 0 and p["days_of_supply_left"] == 40
    assert p["called_on"] is None and p["called_by"] == "" and p["called_notes"] == ""
    assert p["last_picked_up_by"] == "Vanessa", "attribution comes from the session, not the client"
    assert rx["id"] not in due_ids(client, admin_headers)
    history = client.get(f"/api/prescriptions/{rx['id']}/pickups", headers=vanessa_headers).json()
    assert history[0]["picked_up_by"] == "Vanessa" and history[0]["notes"] == "30 tablets" and len(history) == 2

    # the called state is truly gone, not just hidden by the fresh pickup
    with get_conn() as conn:
        row = conn.execute("SELECT called_on FROM prescriptions WHERE id = ?", (rx["id"],)).fetchone()
    assert row["called_on"] is None


def test_second_pickup_same_day_is_rejected_unless_overridden(client, admin_headers, child):
    rx = make_rx(client, admin_headers, child, pickup_days_ago=0)
    r = client.post(f"/api/prescriptions/{rx['id']}/pickups", json={}, headers=admin_headers)
    assert r.status_code == 409 and "override" in r.json()["detail"]
    assert len(client.get(f"/api/prescriptions/{rx['id']}/pickups", headers=admin_headers).json()) == 1
    r = client.post(f"/api/prescriptions/{rx['id']}/pickups", json={"override": True}, headers=admin_headers)
    assert r.status_code == 201
    assert len(client.get(f"/api/prescriptions/{rx['id']}/pickups", headers=admin_headers).json()) == 2


# ---------- /api/status ----------


def test_status_refills_section(client, susan_headers, admin_headers, child):
    due = make_rx(client, admin_headers, child, name="Focalin", pickup_days_ago=28)
    urgent = make_rx(client, admin_headers, child, name="Zyrtec", pickup_days_ago=35)
    fine = make_rx(client, admin_headers, child, name="Vitamin", pickup_days_ago=3)
    called = make_rx(client, admin_headers, child, name="Inhaler", pickup_days_ago=28, days_supply=40)
    client.post(f"/api/prescriptions/{called['id']}/called", json={}, headers=susan_headers)

    body = client.get("/api/status", headers=susan_headers).json()
    by_id = {e["prescription_id"]: e for e in body["refills"]}
    assert due["id"] in by_id and urgent["id"] in by_id
    assert fine["id"] not in by_id and called["id"] not in by_id
    assert by_id[urgent["id"]] == {
        "prescription_id": urgent["id"], "child": "Henry", "prescription": "Zyrtec",
        "refill_status": "urgent", "days_of_supply_left": -5,
    }
    assert set(by_id[due["id"]]) == {"prescription_id", "child", "prescription", "refill_status", "days_of_supply_left"}


# ---------- reminders ----------


def _refill_notes(client, headers):
    return [n for n in client.get("/api/me/notifications", headers=headers).json() if n["kind"] == "refill"]


def test_daily_reminder_to_courtney_and_assignee_without_medication_name(
    client, admin_headers, susan_headers, vanessa_headers, child
):
    susan_id = next(u["id"] for u in client.get("/api/users", headers=admin_headers).json() if u["name"] == "Susan")
    rx = make_rx(client, admin_headers, child, name="Focalin-secret", pickup_days_ago=28, assignee_id=susan_id)
    client.put("/api/admin/reminders", json={"reminder_email": "", "refill_detail_in_notifications": False}, headers=admin_headers)

    created = refills.create_due_notifications()
    assert created >= 2
    assert refills.create_due_notifications() == 0, "one per person per day"

    for headers in (admin_headers, susan_headers):
        notes = [n for n in _refill_notes(client, headers) if n["ref_id"] == rx["id"]]
        assert len(notes) == 1, notes
        assert notes[0]["message"] == "Refill due for Henry, open the app"
        assert "focalin" not in notes[0]["message"].lower()
    assert not [n for n in _refill_notes(client, vanessa_headers) if n["ref_id"] == rx["id"]], "not assigned, not notified"

    # the next day it is sent again, with urgent wording once the supply is out
    tomorrow = date.today() + timedelta(days=2)
    assert refills.create_due_notifications(today=tomorrow) >= 2
    notes = [n for n in _refill_notes(client, admin_headers) if n["ref_id"] == rx["id"]]
    assert len(notes) == 2
    urgent_msg = next(n["message"] for n in notes if n["message"].startswith("URGENT"))
    assert "Henry" in urgent_msg and "focalin" not in urgent_msg.lower()


def test_admin_toggle_allows_full_detail(client, admin_headers, child):
    rx = make_rx(client, admin_headers, child, name="Focalin", pickup_days_ago=28)
    r = client.put("/api/admin/reminders", json={"reminder_email": "", "refill_detail_in_notifications": True}, headers=admin_headers)
    assert r.json()["refill_detail_in_notifications"] is True
    try:
        refills.create_due_notifications()
        notes = [n for n in _refill_notes(client, admin_headers) if n["ref_id"] == rx["id"]]
        assert notes and notes[0]["message"] == "Refill due for Focalin for Henry, open the app"
    finally:
        client.put("/api/admin/reminders", json={"reminder_email": "", "refill_detail_in_notifications": False}, headers=admin_headers)
    assert client.get("/api/admin/reminders", headers=admin_headers).json()["refill_detail_in_notifications"] is False


def test_reminders_endpoint_lists_due_refills(client, admin_headers, child):
    rx = make_rx(client, admin_headers, child, pickup_days_ago=29)
    body = client.get("/api/admin/reminders", headers=admin_headers).json()
    assert rx["id"] in {p["id"] for p in body["due_refills"]}
    assert "due" not in body, "the old one-shot medication reminder list is gone"


# ---------- access ----------


def test_members_can_read_log_and_call_but_not_manage(client, admin_headers, susan_headers, child):
    rx = make_rx(client, admin_headers, child, pickup_days_ago=10)
    assert client.get("/api/prescriptions", headers=susan_headers).status_code == 200
    assert client.get("/api/refills", headers=susan_headers).status_code == 200
    assert client.get("/api/children", headers=susan_headers).status_code == 200
    assert client.post(f"/api/prescriptions/{rx['id']}/called", json={}, headers=susan_headers).status_code == 200
    assert client.post(f"/api/prescriptions/{rx['id']}/pickups", json={}, headers=susan_headers).status_code == 201

    assert client.post("/api/prescriptions", json={"child_id": child["id"], "name": "X"}, headers=susan_headers).status_code == 403
    assert client.patch(f"/api/prescriptions/{rx['id']}", json={"name": "X"}, headers=susan_headers).status_code == 403
    assert client.patch(f"/api/prescriptions/{rx['id']}", json={"active": False}, headers=susan_headers).status_code == 403
    assert client.post("/api/children", json={"name": "X"}, headers=susan_headers).status_code == 403


def test_admin_edits_and_deactivates(client, admin_headers, child):
    rx = make_rx(client, admin_headers, child, pickup_days_ago=29)
    p = client.patch(
        f"/api/prescriptions/{rx['id']}",
        json={"pharmacy": "CVS", "contact_name": "Dr. Lee's office", "contact_phone": "555-0100", "refill_after_days": 25},
        headers=admin_headers,
    ).json()
    assert p["pharmacy"] == "CVS" and p["contact_name"] == "Dr. Lee's office" and p["refill_after_days"] == 25
    assert rx["id"] in due_ids(client, admin_headers)
    p = client.patch(f"/api/prescriptions/{rx['id']}", json={"active": False}, headers=admin_headers).json()
    assert p["active"] is False
    assert rx["id"] not in due_ids(client, admin_headers)
    assert rx["id"] not in {p["id"] for p in client.get("/api/prescriptions", headers=admin_headers).json()}
    assert rx["id"] in {p["id"] for p in client.get("/api/prescriptions?include_inactive=true", headers=admin_headers).json()}


def test_old_medication_routes_are_gone(client, admin_headers):
    assert client.get("/api/medications", headers=admin_headers).status_code == 404


# ---------- MCP tools (called in-process; transport is covered in test_mcp.py) ----------


def test_mcp_log_pickup_two_prescriptions_needs_disambiguation(client, admin_headers, child):
    a = make_rx(client, admin_headers, child, name="Focalin", pickup_days_ago=28)
    b = make_rx(client, admin_headers, child, name="Zyrtec", pickup_days_ago=28)
    res = mcp_server.log_pickup(child="henry")
    assert res["needs_disambiguation"] is True
    assert {c["id"] for c in res["candidates"]} == {a["id"], b["id"]}
    for rx in (a, b):
        assert len(client.get(f"/api/prescriptions/{rx['id']}/pickups", headers=admin_headers).json()) == 1
        assert read_rx(client, admin_headers, rx["id"])["refill_status"] == "refill_due"

    res = mcp_server.log_pickup(child="Henry", prescription="focalin", notes="via Claude")
    assert res["logged"] is True and res["prescription"]["id"] == a["id"] and res["prescription"]["refill_status"] == "ok"
    assert read_rx(client, admin_headers, b["id"])["refill_status"] == "refill_due"


def test_mcp_log_pickup_single_prescription_and_unknown_child(client, admin_headers, child):
    rx = make_rx(client, admin_headers, child, pickup_days_ago=28)
    res = mcp_server.log_pickup(child="Henry", picked_up_on="yesterday")
    assert res["logged"] is True and res["prescription"]["id"] == rx["id"] and res["prescription"]["days_since_pickup"] == 1
    with pytest.raises(ToolError) as e:
        mcp_server.log_pickup(child="Zebediah")
    assert "Henry" in str(e.value)
    with pytest.raises(ToolError, match="override"):
        mcp_server.log_pickup(child="Henry", picked_up_on="yesterday")  # same day again
    assert mcp_server.log_pickup(child="Henry", picked_up_on="yesterday", override=True)["logged"] is True


def test_mcp_list_refills_and_mark_called(client, admin_headers, child):
    due = make_rx(client, admin_headers, child, name="Focalin", pickup_days_ago=28, days_supply=40)
    urgent = make_rx(client, admin_headers, child, name="Zyrtec", pickup_days_ago=31)
    make_rx(client, admin_headers, child, name="Vitamin", pickup_days_ago=2)
    res = mcp_server.list_refills()
    ids = [i["id"] for i in res["items"] if i["child"] == "Henry"]
    assert ids == [urgent["id"], due["id"]] and res["urgent_count"] >= 1

    res = mcp_server.mark_called(child="Henry", prescription="focalin", notes="pharmacy says Friday")
    assert res["called"] is True and res["prescription"]["refill_status"] == "called_waiting"
    assert due["id"] not in [i["id"] for i in mcp_server.list_refills()["items"]]
    assert due["id"] in [i["id"] for i in mcp_server.list_refills(status="called_waiting")["items"]]

    res = mcp_server.mark_called(child="Henry", prescription="zyrtec")
    assert res["prescription"]["refill_status"] == "urgent" and "urgent" in res["message"]
    assert urgent["id"] in [i["id"] for i in mcp_server.list_refills()["items"]]


def test_mcp_list_and_add_prescription(client, admin_headers, child):
    res = mcp_server.add_prescription(
        child="Henry", name="Focalin", contact_name="Dr. Lee's office", contact_phone="555-0100", refill_after_days=28
    )
    assert res["created"] is True and res["prescription"]["contact_name"] == "Dr. Lee's office"
    again = mcp_server.add_prescription(child="henry", name="focalin")
    assert again["created"] is False and again["duplicate"] is True

    new_kid = mcp_server.add_prescription(child="Ava", name="Zyrtec", days_supply=90, refill_after_days=85)
    assert new_kid["created"] is True and new_kid["prescription"]["child"] == "Ava"
    try:
        assert {p["name"] for p in mcp_server.list_prescriptions(child="henry")} == {"Focalin"}
        assert {p["child"] for p in mcp_server.list_prescriptions()} >= {"Henry", "Ava"}
    finally:
        with get_conn() as conn:
            conn.execute("DELETE FROM children WHERE name = 'Ava'")
