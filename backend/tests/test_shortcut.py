"""Siri Shortcut: a person's own key, usable only on /api/shortcut/add, adds one dictated
sentence to the right list and answers in plain text."""

from datetime import date

import pytest

from app import shortcut
from tests.conftest import PASSWORDS, headers_for, user_id

NAMES = ["Courtney", "Magnus", "Susan", "Vanessa"]
TUESDAY = date(2026, 10, 20)


def parse(text):
    return shortcut.parse(text, NAMES, TUESDAY)


# ---------- parsing ----------


@pytest.mark.parametrize(
    "text, person, items, shopping, due",
    [
        ("Susan: call the plumber Friday", "Susan", ["Call the plumber"], False, date(2026, 10, 23)),
        ("susan call the plumber", "Susan", ["Call the plumber"], False, None),
        ("remind Magnus to renew the registration", "Magnus", ["Renew the registration"], False, None),
        ("add return the library books to Vanessa's list", "Vanessa", ["Return the library books"], False, None),
        ("Susan\u2019s list pick up dry cleaning tomorrow", "Susan", ["Pick up dry cleaning"], False, date(2026, 10, 21)),
        ("Vanessa needs to buy diapers", "Vanessa", ["Diapers"], True, None),
        ("put batteries on Magnus's shopping list", "Magnus", ["Batteries"], True, None),
        ("buy milk, eggs and diapers", shortcut.SELF, ["Milk", "Eggs", "Diapers"], True, None),
        ("Buy: paper towels", shortcut.SELF, ["Paper towels"], True, None),
        ("add milk to the shopping list", shortcut.SELF, ["Milk"], True, None),
        ("remind me to call the dentist today", shortcut.SELF, ["Call the dentist"], False, TUESDAY),
        ("Me: renew registration next Tuesday", shortcut.SELF, ["Renew registration"], False, date(2026, 10, 27)),
        ("call the dentist", shortcut.SELF, ["Call the dentist"], False, None),
        # a word before a comma is only a person if it's exactly someone's name
        ("Milk, eggs, bread", shortcut.SELF, ["Milk, eggs, bread"], False, None),
        ("Mac and cheese", shortcut.SELF, ["Mac and cheese"], False, None),
    ],
)
def test_parse(text, person, items, shopping, due):
    p = parse(text)
    assert (p.person, p.items, p.shopping, p.due_on) == (person, items, shopping, due)


@pytest.mark.parametrize("text", ["Suzanne: call the plumber", "remind Bob to x", "add x to Bob's list"])
def test_unknown_name_asks_and_lists_everyone(text):
    with pytest.raises(shortcut.Reply) as e:
        parse(text)
    msg = e.value.message
    assert "Nothing was added" in msg and "Courtney, Magnus, Susan, Vanessa" in msg


def test_confident_misspelling_is_matched():
    assert parse("remind Vanesa to fold towels").person == "Vanessa"


def test_close_misspelling_suggests_the_nearest_name():
    with pytest.raises(shortcut.Reply) as e:
        parse("Suzanne: call the plumber")
    assert "Did you mean Susan?" in e.value.message


@pytest.mark.parametrize("text", ["", "   ", "Susan", "Susan's list", "Susan: ", "remind me to"])
def test_nothing_to_add_is_a_question(text):
    with pytest.raises(shortcut.Reply):
        parse(text)


# ---------- endpoint ----------


def make_key(client, name) -> str:
    r = client.post("/api/me/shortcut", headers=headers_for(client, name))
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["url"].endswith("/api/shortcut/add")
    assert body["authorization"] == f"Bearer {body['key']}"
    return body["authorization"]


def say(client, auth, text, **kw):
    return client.post("/api/shortcut/add", headers={"Authorization": auth}, json={"text": text}, **kw)


def list_titles(client, name):
    r = client.get("/api/me/list", headers=headers_for(client, name))
    return {i["title"]: i for i in r.json()["items"]}


def test_adds_to_own_list_and_answers_in_plain_text(client):
    auth = make_key(client, "Susan")
    r = say(client, auth, "remind me to water the ferns Friday")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/plain")
    assert r.text.startswith("Added Water the ferns to your list, due Friday")
    item = list_titles(client, "Susan")["Water the ferns"]
    assert item["kind"] == "task" and item["due_on"] is not None


def test_shopping_batch_goes_on_shopping_list(client):
    auth = make_key(client, "Vanessa")
    r = say(client, auth, "buy wipes, juice boxes and string cheese")
    assert r.text == "Added Wipes, Juice boxes and String cheese to your shopping list."
    items = list_titles(client, "Vanessa")
    assert all(items[n]["kind"] == "shopping" for n in ("Wipes", "Juice boxes", "String cheese"))
    shopping = client.get("/api/shopping-items", headers=headers_for(client, "Courtney")).json()
    wipes = next(s for s in shopping if s["name"] == "Wipes")
    assert wipes["added_by"] == "Vanessa"


def test_admin_puts_it_on_someone_elses_list_and_they_are_notified(client):
    auth = make_key(client, "Courtney")
    r = say(client, auth, "Susan: drop off the recycling")
    assert r.text == "Added Drop off the recycling to Susan's list."
    assert "Drop off the recycling" in list_titles(client, "Susan")
    notes = client.get("/api/me/notifications", headers=headers_for(client, "Susan")).json()
    assert any(n["message"] == "Courtney put a task on your list: Drop off the recycling" for n in notes)


def test_member_cannot_put_things_on_someone_elses_list(client):
    auth = make_key(client, "Susan")
    r = say(client, auth, "Vanessa: mop the porch")
    assert r.status_code == 200
    assert "Only an admin" in r.text and "Nothing was added" in r.text
    assert "Mop the porch" not in list_titles(client, "Vanessa")


def test_unknown_name_adds_nothing(client):
    auth = make_key(client, "Courtney")
    before = list_titles(client, "Courtney")
    r = say(client, auth, "Suzanne: clean the gutters")
    assert "Did you mean Susan?" in r.text
    assert list_titles(client, "Courtney") == before


def test_duplicate_is_not_added_twice(client):
    auth = make_key(client, "Magnus")
    assert say(client, auth, "remind me to rotate the tires").text.startswith("Added")
    r = say(client, auth, "remind me to rotate tires")
    assert r.text == "Already on your list: Rotate the tires."
    assert sum(1 for t in list_titles(client, "Magnus") if "otate" in t) == 1


def test_form_and_raw_bodies_work(client):
    auth = make_key(client, "Magnus")
    r = client.post("/api/shortcut/add", headers={"Authorization": auth}, data={"text": "remind me to oil the hinges"})
    assert r.text.startswith("Added Oil the hinges")
    r = client.post("/api/shortcut/add", headers={"Authorization": auth, "Content-Type": "text/plain"}, content="remind me to fix the gate")
    assert r.text.startswith("Added Fix the gate")


def test_long_item_keeps_full_text(client):
    auth = make_key(client, "Magnus")
    long = "Call " + "the very patient insurance agent " * 6
    assert say(client, auth, long).text.startswith("Added")
    task = next(i for t, i in list_titles(client, "Magnus").items() if t.startswith("Call the very patient"))
    assert len(task["title"]) == 120 and task["notes"].strip() == long.strip()


# ---------- the key ----------


def test_key_only_works_on_the_shortcut_endpoint(client):
    auth = make_key(client, "Susan")
    for method, path in [("GET", "/api/status"), ("GET", "/api/me/list"), ("POST", "/api/me/shortcut"), ("GET", "/api/admin/users")]:
        assert client.request(method, path, headers={"Authorization": auth}).status_code == 401, path


def test_session_token_and_no_token_are_refused_on_shortcut_endpoint(client):
    for headers in ({}, headers_for(client, "Courtney"), {"Authorization": "Bearer hk_not-a-real-key"}):
        r = client.post("/api/shortcut/add", headers=headers, json={"text": "remind me to x"})
        assert r.status_code == 401
        assert "Make a new Siri key" in r.text


def test_new_key_replaces_old_and_revoke_turns_it_off(client):
    old = make_key(client, "Vanessa")
    new = make_key(client, "Vanessa")
    assert say(client, old, "remind me to a").status_code == 401
    assert say(client, new, "remind me to fold towels").status_code == 200
    h = headers_for(client, "Vanessa")
    status = client.get("/api/me/shortcut", headers=h).json()
    assert status["has_key"] and status["last_used_at"]
    assert "key" not in status
    assert client.delete("/api/me/shortcut", headers=h).json() == {"revoked": True}
    assert say(client, new, "remind me to b").status_code == 401
    assert client.get("/api/me/shortcut", headers=h).json()["has_key"] is False


def test_password_reset_or_deactivation_kills_the_key(client, admin_headers):
    vid = user_id(client, admin_headers, "Vanessa")
    auth = make_key(client, "Vanessa")
    r = client.patch(f"/api/admin/users/{vid}", headers=admin_headers, json={"password": "vanessa-new-password"})
    assert r.status_code == 200, r.text
    assert say(client, auth, "remind me to c").status_code == 401
    assert client.get("/api/me/shortcut", headers=headers_for(client, "Vanessa", "vanessa-new-password")).json()["has_key"] is False
    client.patch(f"/api/admin/users/{vid}", headers=admin_headers, json={"password": PASSWORDS["Vanessa"]})

    auth = make_key(client, "Vanessa")
    client.patch(f"/api/admin/users/{vid}", headers=admin_headers, json={"active": False})
    try:
        assert say(client, auth, "remind me to d").status_code == 401
    finally:
        client.patch(f"/api/admin/users/{vid}", headers=admin_headers, json={"active": True})


def test_only_the_hash_is_stored(client):
    from app.db import get_conn

    auth = make_key(client, "Courtney")
    key = auth.removeprefix("Bearer ")
    with get_conn() as conn:
        rows = [tuple(r) for r in conn.execute("SELECT * FROM shortcut_keys")]
    assert rows and not any(key in str(v) for r in rows for v in r)


def test_connector_cannot_make_a_key(client, connector_headers):
    assert client.post("/api/me/shortcut", headers=connector_headers).status_code == 403
