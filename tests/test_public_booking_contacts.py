"""Public booking with a phone and an email, and finding the card by any of a client's contacts."""
from app.api.core import client_contacts
from app.api.crud import crud_public_booking
from app.db.models.client import Client
from app.db.models.client_business_relation import ClientBusinessRelation
from app.db.models.visit import Visit
from tests.test_public_booking_acceptance import _body, _book, _local_utc, _ok, _salon


def _card(db, business_id, name, *contacts):
    card = Client(full_name=name)
    db.add(card)
    db.flush()
    client_contacts.set_contacts(db, card, [client_contacts.ContactValue(k, v) for k, v in contacts])
    db.add(ClientBusinessRelation(client_id=card.id, business_id=business_id))
    db.commit()
    return card


def test_guest_can_leave_both_a_phone_and_an_email(client, db, make_user, headers_for):
    salon = _salon(client, make_user, headers_for, "Both")
    start, _day = _local_utc()
    booked = _ok(_book(client, salon, _body(salon, start, "both-contacts-1", phone="+420 777 123 456")), "book")

    visit = db.get(Visit, booked["id"])
    card = db.get(Client, visit.client_id)
    assert card.contact_email == "guest@example.com"
    assert card.contact_phone == "+420777123456"
    assert {(c.kind, c.value, c.is_primary) for c in card.contacts} == {
        ("email", "guest@example.com", True), ("phone", "+420777123456", True)}


def test_guest_still_needs_at_least_one_contact(client, make_user, headers_for):
    salon = _salon(client, make_user, headers_for, "Nocontact")
    start, _day = _local_utc()
    body = {k: v for k, v in _body(salon, start, "no-contact-01").items() if k != "email"}
    response = _book(client, salon, body)
    assert response.status_code == 422 and "contact_choice" in response.text


def test_second_number_finds_the_existing_card(client, db, make_user, headers_for):
    salon = _salon(client, make_user, headers_for, "Second")
    business_id = salon["business"]["id"]
    card = _card(db, business_id, "Jana", ("phone", "+420777000111"), ("phone", "+420777000222"))
    start, _day = _local_utc()
    body = {k: v for k, v in _body(salon, start, "second-num-01").items() if k != "email"}
    booked = _ok(_book(client, salon, {**body, "phone": "+420777000222"}), "book")
    assert db.get(Visit, booked["id"]).client_id == card.id


def test_shared_family_phone_prefers_primary_then_latest_visit(db, make_user, client, headers_for):
    salon = _salon(client, make_user, headers_for, "Family")
    business_id = salon["business"]["id"]
    kid = _card(db, business_id, "Kid", ("phone", "+420777000999"), ("phone", "+420777000111"))
    mum = _card(db, business_id, "Mum", ("phone", "+420777000111"))
    dad = _card(db, business_id, "Dad", ("phone", "+420777000111"))
    query = crud_public_booking._business_clients_query(db, business_id)

    # the number is primary on two cards and secondary on the kid's - and nobody has visits yet
    order = [c.id for c in client_contacts.find_clients(db, query, phone="+420777000111")]
    assert order == [mum.id, dad.id, kid.id]

    from datetime import datetime
    db.add(Visit(client_id=dad.id, business_id=business_id, specialist_id=salon["owner"].id,
                 date_start=datetime(2030, 1, 1, 10), date_end=datetime(2030, 1, 1, 11), status="planned"))
    db.commit()
    order = [c.id for c in client_contacts.find_clients(db, query, phone="+420777000111")]
    assert order == [dad.id, mum.id, kid.id]


def test_email_beats_a_shared_phone_and_nothing_is_planted(client, db, make_user, headers_for):
    salon = _salon(client, make_user, headers_for, "Plant")
    business_id = salon["business"]["id"]
    owner = _card(db, business_id, "Owner", ("phone", "+420777000111"), ("email", "owner@example.com"))
    start, _day = _local_utc()
    # someone who knows the number but has their own email gets their own card
    booked = _ok(_book(client, salon, _body(salon, start, "plant-key-01", phone="+420777000111",
                                             email="stranger@example.com")), "book")
    visit_client = db.get(Visit, booked["id"]).client_id
    assert visit_client != owner.id
    db.expire_all()
    assert {c.value for c in db.get(Client, owner.id).contacts} == {"+420777000111", "owner@example.com"}


def test_phone_only_card_is_found_by_a_booking_that_brings_an_email(client, db, make_user, headers_for):
    salon = _salon(client, make_user, headers_for, "Phoneonly")
    business_id = salon["business"]["id"]
    card = _card(db, business_id, "Walk-in", ("phone", "+420777000111"))
    start, _day = _local_utc()
    booked = _ok(_book(client, salon, _body(salon, start, "phone-only-01", phone="+420777000111")), "book")
    assert db.get(Visit, booked["id"]).client_id == card.id
    db.expire_all()
    # the typed email is not written onto the old card
    assert db.get(Client, card.id).contact_email is None


def test_another_salons_card_is_never_matched(client, db, make_user, headers_for):
    one = _salon(client, make_user, headers_for, "Uno")
    two = _salon(client, make_user, headers_for, "Due")
    _card(db, one["business"]["id"], "Elsewhere", ("phone", "+420777000111"), ("phone", "+420777000222"))
    query = crud_public_booking._business_clients_query(db, two["business"]["id"])
    assert client_contacts.find_clients(db, query, phone="+420777000222") == []
