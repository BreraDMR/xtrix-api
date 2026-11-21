"""Several phones and emails on one client card."""
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.db.models.client import Client
from app.db.models.client_contact import ClientContact


def _create(client, headers, **payload):
    payload.setdefault("full_name", "Jana Nováková")
    r = client.post("/clients/", json=payload, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


def _values(card, kind):
    return [c["value"] for c in card["contacts"] if c["kind"] == kind]


def test_old_style_create_gets_a_primary_contact(client, auth_headers):
    card = _create(client, auth_headers, contact_phone="+420 777 000 111", contact_email="Jana@Example.com")

    assert card["contact_phone"] == "+420 777 000 111"
    assert _values(card, "phone") == ["+420777000111"]
    assert _values(card, "email") == ["jana@example.com"]
    assert all(c["is_primary"] and c["notify"] for c in card["contacts"])


def test_phone_and_email_together_with_extra_numbers(client, auth_headers):
    card = _create(client, auth_headers, contacts=[
        {"kind": "phone", "value": "+420777000111"},
        {"kind": "phone", "value": "+420 777 000 222", "label": "work", "is_primary": True},
        {"kind": "email", "value": "jana@example.com"},
    ])

    assert card["contact_phone"] == "+420777000222"
    assert card["contact_email"] == "jana@example.com"
    phones = [c for c in card["contacts"] if c["kind"] == "phone"]
    assert [(p["value"], p["is_primary"], p["label"]) for p in phones] == [
        ("+420777000222", True, "work"), ("+420777000111", False, None)]

    fetched = client.get(f"/clients/{card['id']}", headers=auth_headers).json()
    assert fetched["contacts"] == card["contacts"]


@pytest.mark.parametrize("contacts,code", [
    ([{"kind": "phone", "value": f"+42077700011{i}"} for i in range(6)], "too_many_phones"),
    ([{"kind": "email", "value": f"j{i}@example.com"} for i in range(4)], "too_many_emails"),
    ([{"kind": "phone", "value": "+420777000111"}, {"kind": "phone", "value": "+420 777 000 111"}], "duplicate_contact"),
    ([{"kind": "phone", "value": "+420777000111", "is_primary": True},
      {"kind": "phone", "value": "+420777000222", "is_primary": True}], "multiple_primary"),
    ([{"kind": "phone", "value": "777000111"}], "invalid_phone"),
    ([{"kind": "email", "value": "not-an-email"}], "invalid_email"),
])
def test_bad_contact_lists_are_rejected(client, auth_headers, contacts, code):
    r = client.post("/clients/", json={"full_name": "X", "contacts": contacts}, headers=auth_headers)
    assert r.status_code == 422, r.text
    assert code in r.text


def test_old_field_must_agree_with_the_list(client, auth_headers):
    r = client.post("/clients/", json={
        "full_name": "X", "contact_phone": "+420777000999",
        "contacts": [{"kind": "phone", "value": "+420777000111"}],
    }, headers=auth_headers)
    assert r.status_code == 422 and "contacts_mismatch" in r.text


def test_update_replaces_the_list_and_keeps_rows_that_stay(client, auth_headers):
    card = _create(client, auth_headers, contacts=[
        {"kind": "phone", "value": "+420777000111"},
        {"kind": "phone", "value": "+420777000222"},
    ])
    kept_id = card["contacts"][1]["id"]

    r = client.put(f"/clients/{card['id']}", json={"contacts": [
        {"kind": "phone", "value": "+420777000222", "is_primary": True},
        {"kind": "email", "value": "jana@example.com"},
    ]}, headers=auth_headers)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["contact_phone"] == "+420777000222"
    assert out["contact_email"] == "jana@example.com"
    assert _values(out, "phone") == ["+420777000222"]
    assert out["contacts"][0]["id"] == kept_id


def test_old_style_update_moves_the_primary_and_keeps_the_rest(client, auth_headers):
    card = _create(client, auth_headers, contacts=[
        {"kind": "phone", "value": "+420777000111"},
        {"kind": "phone", "value": "+420777000222"},
    ])

    # the old app edits the one number it knows about
    r = client.put(f"/clients/{card['id']}", json={"contact_phone": "+420777000333"}, headers=auth_headers)
    assert r.status_code == 200, r.text
    assert _values(r.json(), "phone") == ["+420777000333", "+420777000222"]

    # and clearing it lets the next number take over
    r = client.put(f"/clients/{card['id']}", json={"contact_phone": None}, headers=auth_headers)
    assert r.json()["contact_phone"] == "+420777000222"
    assert _values(r.json(), "phone") == ["+420777000222"]


def test_leaving_contacts_out_keeps_them(client, auth_headers):
    card = _create(client, auth_headers, contacts=[
        {"kind": "phone", "value": "+420777000111"},
        {"kind": "phone", "value": "+420777000222"},
    ])
    r = client.put(f"/clients/{card['id']}", json={"full_name": "Jana N."}, headers=auth_headers)
    assert _values(r.json(), "phone") == ["+420777000111", "+420777000222"]


def test_second_number_finds_the_card(client, auth_headers):
    card = _create(client, auth_headers, contacts=[
        {"kind": "phone", "value": "+420777000111"},
        {"kind": "phone", "value": "+420777000222"},
        {"kind": "email", "value": "jana@example.com"},
        {"kind": "email", "value": "jana.work@example.com"},
    ])

    found = client.get("/clients/search", params={"phone": "+420 777 000 222"}, headers=auth_headers)
    assert found.status_code == 200 and found.json()["id"] == card["id"]
    found = client.get("/clients/search", params={"email": "JANA.WORK@example.com"}, headers=auth_headers)
    assert found.json()["id"] == card["id"]

    listed = client.get("/clients/", params={"phone": "+420777000222"}, headers=auth_headers).json()
    assert [c["id"] for c in listed["items"]] == [card["id"]]
    listed = client.get("/clients/", params={"q": "jana.work"}, headers=auth_headers).json()
    assert [c["id"] for c in listed["items"]] == [card["id"]]

    # and a new card with that second number is a duplicate
    r = client.post("/clients/", json={"full_name": "Someone", "contact_phone": "+420777000222"}, headers=auth_headers)
    assert r.status_code == 409 and r.json()["error"]["detail"]["existing_client_id"] == card["id"]


def test_extra_number_on_a_new_card_is_checked_too(client, auth_headers):
    card = _create(client, auth_headers, contact_phone="+420777000111")
    r = client.post("/clients/", json={"full_name": "Someone", "contacts": [
        {"kind": "phone", "value": "+420777000999"},
        {"kind": "phone", "value": "+420777000111"},
    ]}, headers=auth_headers)
    assert r.status_code == 409 and r.json()["error"]["detail"]["existing_client_id"] == card["id"]


def test_database_caps_phones_at_five(db):
    card = Client(full_name="Cap")
    db.add(card)
    db.flush()
    for i in range(5):
        db.add(ClientContact(client_id=card.id, kind="phone", value=f"+42077700010{i}"))
    db.flush()
    db.add(ClientContact(client_id=card.id, kind="phone", value="+420777000199"))
    with pytest.raises(DBAPIError):
        db.flush()
    db.rollback()


def test_database_allows_one_primary_per_kind(db):
    card = Client(full_name="Primary")
    db.add(card)
    db.flush()
    db.add(ClientContact(client_id=card.id, kind="phone", value="+420777000101", is_primary=True))
    db.add(ClientContact(client_id=card.id, kind="email", value="a@example.com", is_primary=True))
    db.flush()
    db.add(ClientContact(client_id=card.id, kind="phone", value="+420777000102", is_primary=True))
    with pytest.raises(DBAPIError):
        db.flush()
    db.rollback()


def test_contacts_go_with_the_card(db):
    card = Client(full_name="Gone")
    db.add(card)
    db.flush()
    db.add(ClientContact(client_id=card.id, kind="phone", value="+420777000101"))
    db.commit()
    db.execute(text("DELETE FROM clients WHERE id = :id"), {"id": card.id})
    db.commit()
    assert db.query(ClientContact).count() == 0


def test_family_card_with_a_shared_number_needs_a_yes(client, auth_headers):
    mum = _create(client, auth_headers, full_name="Mum", birth_date="1980-01-01", contact_phone="+420777000111")
    r = client.post("/clients/", json={"full_name": "Kid", "contact_phone": "+420777000111"}, headers=auth_headers)
    assert r.status_code == 409
    kid = _create(client, auth_headers, full_name="Kid", contact_phone="+420777000111", allow_shared_contact=True)
    assert kid["id"] != mum["id"]
    # the same person twice is still refused
    r = client.post("/clients/", json={"full_name": "Mum", "birth_date": "1980-01-01",
                                       "contact_phone": "+420777000111", "allow_shared_contact": True},
                    headers=auth_headers)
    assert r.status_code == 409
