"""CPA-19 access matrix over HTTP: two people, two salons, one shared mailbox.

Most rows of the CPA-19 matrix already have their own focused tests (see the
card in TASKS.md for the "covered where" list). This file glues the missing
cross-cutting case together through the real routes: imported duplicate
cards with one email, a mailbox shared by two salons, an invite sent to the
wrong address, someone else's visit_id, and a service deleted after the visit.
"""
from datetime import timedelta

import pytest
from sqlalchemy import update

from app.api.core import visitor_sessions
from app.api.core.clock import utcnow
from app.db.models.business import Business
from app.db.models.client import Client
from app.db.models.client_business_relation import ClientBusinessRelation
from app.db.models.service import Service
from app.db.models.subscription import Subscription
from app.db.models.subscription_plan import SubscriptionPlan
from app.db.models.user import User
from app.db.models.visit import Visit
from app.db.models.visit_item import VisitItem
from app.db.models.visitor_access import VisitorClaim, VisitorGrant
from app.db.models.visitor_account import VisitorAccount

SHARED = "family.mailbox@example.com"
SECRET_NOTE = "private-crm-note-do-not-leak"
# Fields that live on Visit/Client rows but must never reach a visitor.
INTERNAL = ("client_id", "specialist_id", "service_id", "note", "photo_url", "payment_method",
            "manual_payment_recorded_at", "deleted_at", "archived_at", "contact_email", "contact_phone",
            "access_password", "qr_code", "owner_id", "created_at")


def _salon(db, name, staff_email):
    business = Business(name=name, business_type="salon", timezone="Europe/Prague", currency="CZK")
    db.add(business)
    db.flush()
    staff = User(full_name=f"{name} Staff", email=staff_email, hashed_password="synthetic",
                 business_id=business.id, role="business_owner")
    db.add(staff)
    db.flush()
    business.owner_id = staff.id
    plan = SubscriptionPlan(code=f"matrix_{business.id}", name="Synthetic", period_days=30, price=200)
    db.add(plan)
    db.flush()
    db.add(Subscription(user_id=staff.id, plan_id=plan.id, status="active",
                        current_period_end=utcnow() + timedelta(days=30), is_trial=False))
    service = Service(specialist_id=staff.id, business_id=business.id, name=f"{name} Cut",
                      duration_minutes=60, price=500)
    db.add(service)
    db.flush()
    return business, staff, service


def _card(db, business, staff, name):
    card = Client(full_name=name, owner_id=staff.id, contact_email=SHARED)
    db.add(card)
    db.flush()
    db.add(ClientBusinessRelation(client_id=card.id, business_id=business.id))
    return card


def _visit(db, business, staff, service, card, days_ago):
    start = utcnow() - timedelta(days=days_ago)
    visit = Visit(business_id=business.id, client_id=card.id, specialist_id=staff.id, service_id=service.id,
                  service_name=service.name, date_start=start, date_end=start + timedelta(hours=1),
                  price=500, currency_snapshot="CZK", status="completed", note=SECRET_NOTE,
                  payment_method="cash")
    db.add(visit)
    db.flush()
    db.add(VisitItem(visit_id=visit.id, position=0, service_id=service.id, specialist_id=staff.id,
                     active=True, service_name=service.name, price=500, duration_minutes=60,
                     date_start=start, date_end=start + timedelta(hours=1)))
    return visit


def _person(db, email):
    account = VisitorAccount(email_canonical=email, email_verified_at=utcnow())
    db.add(account)
    db.flush()
    return account, {"X-Visitor-Token": visitor_sessions.create_session(db, account)[1]}


@pytest.fixture
def matrix(db, headers_for):
    a, a_staff, a_service = _salon(db, "Matrix A", "matrix.a@example.com")
    b, b_staff, b_service = _salon(db, "Matrix B", "matrix.b@example.com")
    # Salon A imported the same person twice (one mailbox, two cards); salon B
    # has a different person who uses the very same family mailbox.
    anna = _card(db, a, a_staff, "Anna")
    anna_dup = _card(db, a, a_staff, "Anna (import)")
    mom = _card(db, b, b_staff, "Anna's mom")
    w = {
        "a": a.id, "b": b.id,
        "anna": [_visit(db, a, a_staff, a_service, anna, d).id for d in (3, 2)],
        "anna_dup": _visit(db, a, a_staff, a_service, anna_dup, 4).id,
        "mom": _visit(db, b, b_staff, b_service, mom, 5).id,
        "anna_card": anna.id, "mom_card": mom.id, "a_service": a_service.id,
    }
    anna_account, w["anna_h"] = _person(db, SHARED)
    mom_account, w["mom_h"] = _person(db, "mom.own@example.com")
    w["anna_acc"], w["mom_acc"] = anna_account.id, mom_account.id
    db.commit()
    w["a_crm"] = headers_for(db.get(User, a_staff.id))
    w["b_crm"] = headers_for(db.get(User, b_staff.id))
    return w


def _invite(client, crm, card, email):
    response = client.post(f"/clients/{card}/visitor-invitations", json={"email": email}, headers=crm)
    assert response.status_code == 201, response.text
    return response.json()["token"]


def _accept(client, headers, token):
    return client.post("/visitor/claims/client-invite", json={"token": token}, headers=headers)


def _ids(client, headers):
    response = client.get("/visitor/visits", headers=headers)
    assert response.status_code == 200, response.text
    return sorted(row["id"] for row in response.json()["entries"])


def _no_internals(payload):
    if isinstance(payload, dict):
        assert not set(payload) & set(INTERNAL), set(payload) & set(INTERNAL)
        for value in payload.values():
            _no_internals(value)
    elif isinstance(payload, list):
        for value in payload:
            _no_internals(value)


def test_shared_mailbox_and_import_duplicates_never_widen_access(client, db, matrix):
    w = matrix
    # A verified mailbox alone grants nothing, even though three cards carry it.
    assert _ids(client, w["anna_h"]) == [] and _ids(client, w["mom_h"]) == []

    assert _accept(client, w["anna_h"], _invite(client, w["a_crm"], w["anna_card"], SHARED)).status_code == 204
    # Exactly the invited card: not the imported duplicate, not salon B's card.
    assert _ids(client, w["anna_h"]) == sorted(w["anna"])
    for hidden in (w["anna_dup"], w["mom"]):
        assert client.get(f"/visitor/visits/{hidden}", headers=w["anna_h"]).status_code == 404

    # Salon B invites the mom to her own address; the shared-mailbox account
    # can't use that link, the right person can retry and gets only her card.
    mom_token = _invite(client, w["b_crm"], w["mom_card"], "mom.own@example.com")
    assert _accept(client, w["anna_h"], mom_token).status_code == 400
    assert db.query(VisitorGrant).filter_by(account_id=w["mom_acc"]).count() == 0
    assert _accept(client, w["mom_h"], mom_token).status_code == 204
    assert _accept(client, w["mom_h"], mom_token).status_code == 204
    assert _ids(client, w["mom_h"]) == [w["mom"]]
    assert _ids(client, w["anna_h"]) == sorted(w["anna"])
    assert db.query(VisitorGrant).count() == 2 and db.query(VisitorAccount).count() == 2

    # Someone else's visit looks exactly like a visit that doesn't exist.
    foreign = client.get(f"/visitor/visits/{w['anna'][0]}", headers=w["mom_h"])
    missing = client.get("/visitor/visits/999999999", headers=w["mom_h"])
    assert foreign.status_code == missing.status_code == 404
    assert foreign.json() == missing.json()
    for action in ("cancel", "confirm"):
        response = client.post(f"/visitor/visits/{w['anna'][0]}/{action}", headers=w["mom_h"])
        assert response.status_code == 404, response.text
    # Body or query ids don't switch the scope either.
    assert _ids(client, w["mom_h"]) == [w["mom"]]
    assert client.get("/visitor/visits", headers=w["mom_h"], params={"business_id": w["a"]}).json()["entries"] == []

    # A spent invite is still single-use for everybody else.
    anna_token = _invite(client, w["a_crm"], w["anna_card"], SHARED)
    assert _accept(client, w["mom_h"], anna_token).status_code == 400
    assert db.query(VisitorClaim).filter(VisitorClaim.consumed_at.is_(None)).count() == 1


def test_deleted_service_keeps_history_and_answers_have_no_internal_fields(client, db, matrix):
    w = matrix
    assert _accept(client, w["anna_h"], _invite(client, w["a_crm"], w["anna_card"], SHARED)).status_code == 204
    db.execute(update(Service).where(Service.id == w["a_service"]).values(deleted_at=utcnow(), is_active=False))
    db.commit()

    page = client.get("/visitor/visits", headers=w["anna_h"])
    assert page.status_code == 200, page.text
    assert sorted(row["id"] for row in page.json()["entries"]) == sorted(w["anna"])
    detail = client.get(f"/visitor/visits/{w['anna'][0]}", headers=w["anna_h"])
    assert detail.status_code == 200, detail.text
    body = detail.json()
    # The sold name is a snapshot, deleting the catalog row doesn't blank it.
    assert body["service_name"] == "Matrix A Cut"
    assert [item["service_name"] for item in body["items"]] == ["Matrix A Cut"]
    assert body["price"]["currency"] == "CZK"
    for response in (page, detail):
        assert SECRET_NOTE not in response.text and SHARED not in response.text
        assert response.headers.get("cache-control") == "no-store"
        _no_internals(response.json())
