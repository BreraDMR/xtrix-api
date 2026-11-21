"""Extra phones through import/export, and which contact messages go to."""
import csv
import io
from types import SimpleNamespace

from app.api.core import client_contacts
from app.api.core.export import prepare_client_export_data
from app.db.models.client import Client
from app.db.models.client_contact import ClientContact
from tests.tenant_world import build_tenant
from tests.test_imports import _confirm, _csv, _prepare

HEADERS = ["Name", "Phone", "Phone 2", "Phone 3", "Email"]


def _import(client, headers, rows, policy="skip"):
    batch = _prepare(client, headers, _csv(rows, headers=HEADERS), policy=policy)
    confirmed = _confirm(client, headers, batch)
    assert confirmed.status_code == 200, confirmed.text
    return batch, confirmed.json()["batch"]


def test_import_reads_extra_phone_columns(client, db, make_user, headers_for):
    world = build_tenant(client, make_user, headers_for, "Alpha", "+42070061")
    batch, result = _import(client, world["headers"], [
        ["New Person", "+420700619001", "+420 700 619 002", "", "new@example.com"],
    ])
    assert batch["suggested_mapping"]["phone2"] == "Phone 2"
    assert result["created_count"] == 1, result

    card = db.query(Client).filter(Client.full_name == "New Person").one()
    assert card.contact_phone == "+420700619001"
    assert [(c.kind, c.value, c.is_primary) for c in card.contacts] == [
        ("phone", "+420700619001", True), ("phone", "+420700619002", False), ("email", "new@example.com", True)]


def test_import_matches_a_card_by_its_second_number(client, db, make_user, headers_for):
    world = build_tenant(client, make_user, headers_for, "Alpha", "+42070062")
    card_id = world["client"]["id"]
    r = client.put(f"/clients/{card_id}", json={"contacts": [
        {"kind": "phone", "value": world["client"]["contact_phone"]},
        {"kind": "phone", "value": "+420700629999"},
    ]}, headers=world["headers"])
    assert r.status_code == 200, r.text

    _batch, result = _import(client, world["headers"], [["Same Person", "+420700629999", "", "", ""]])
    assert result["skipped_count"] == 1 and result["created_count"] == 0, result


def test_shared_number_in_an_import_is_ambiguous_not_guessed(client, db, make_user, headers_for):
    world = build_tenant(client, make_user, headers_for, "Alpha", "+42070063")
    for name in ("Mum", "Kid"):
        r = client.post("/clients/", json={"full_name": name, "contacts": [
            {"kind": "phone", "value": f"+42070063{len(name)}{len(name)}11"},
            {"kind": "phone", "value": "+420700638888"},
        ], "allow_shared_contact": True}, headers=world["headers"])
        assert r.status_code == 200, r.text
    batch = _prepare(client, world["headers"], _csv([["Who", "+420700638888", "", "", ""]], headers=HEADERS),
                     policy="update")
    assert batch["error_count"] == 1, batch


def test_export_lists_every_contact(db):
    card = Client(full_name="Many")
    db.add(card)
    db.flush()
    client_contacts.set_contacts(db, card, [
        client_contacts.ContactValue("phone", "+420700000001"),
        client_contacts.ContactValue("phone", "+420700000002"),
        client_contacts.ContactValue("phone", "+420700000003"),
        client_contacts.ContactValue("email", "a@example.com"),
        client_contacts.ContactValue("email", "b@example.com"),
    ])
    db.commit()
    row = prepare_client_export_data([card])[0]
    assert row["Contact Phone"] == "+420700000001"
    assert (row["Phone 2"], row["Phone 3"], row["Phone 4"], row["Phone 5"]) == (
        "+420700000002", "+420700000003", None, None)
    assert (row["Email 2"], row["Email 3"]) == ("b@example.com", None)


def test_export_csv_has_the_extra_columns(client, make_user, headers_for):
    world = build_tenant(client, make_user, headers_for, "Alpha", "+42070064")
    client.put(f"/clients/{world['client']['id']}", json={"contacts": [
        {"kind": "phone", "value": world["client"]["contact_phone"]},
        {"kind": "phone", "value": "+420700647777"},
    ]}, headers=world["headers"])
    r = client.get("/clients/export/csv", headers=world["headers"])
    assert r.status_code == 200, r.text
    rows = list(csv.DictReader(io.StringIO(r.text)))
    assert rows[0]["Phone 2"].lstrip("'") == "+420700647777"


def test_messages_go_to_primary_unless_it_is_muted(db):
    card = Client(full_name="Mute", contact_phone="+420700000001", contact_email="a@example.com")
    db.add(card)
    db.flush()
    assert client_contacts.notify_phone(card) == "+420700000001"  # no rows yet: the old column
    client_contacts.set_contacts(db, card, [
        client_contacts.ContactValue("phone", "+420700000001", is_primary=True, notify=False),
        client_contacts.ContactValue("phone", "+420700000002"),
        client_contacts.ContactValue("email", "a@example.com", notify=False),
    ])
    db.commit()
    assert client_contacts.notify_phone(card) == "+420700000002"
    assert client_contacts.notify_email(card) is None
    # stand-ins without rows still work
    assert client_contacts.notify_email(SimpleNamespace(contact_email=" x@example.com ")) == "x@example.com"


def test_portal_link_can_be_asked_for_with_a_second_email(client, db, make_user, headers_for):
    from app.db.models.notification_outbox import NotificationOutbox

    world = build_tenant(client, make_user, headers_for, "Alpha", "+42070065")
    owner_headers = world["headers"]
    settings = client.put("/businesses/me/public-booking", json={"is_enabled": True}, headers=owner_headers)
    assert settings.status_code == 200, settings.text
    client.put(f"/clients/{world['client']['id']}", json={"contacts": [
        {"kind": "email", "value": world["client"]["contact_email"]},
        {"kind": "email", "value": "second@example.com"},
    ]}, headers=owner_headers)

    r = client.post(f"/public/client-portal/{settings.json()['slug']}/request", json={"email": "Second@example.com"})
    assert r.status_code == 202, r.text
    rows = db.query(NotificationOutbox).filter(NotificationOutbox.kind == "client_portal_login").all()
    assert [row.recipient for row in rows] == ["second@example.com"]
    assert db.query(ClientContact).filter(ClientContact.value == "second@example.com").count() == 1
