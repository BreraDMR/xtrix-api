"""Pilot access is enforced at endpoints, including direct export requests."""
from datetime import timedelta

import pytest

from app.api.core.clock import utcnow
from app.api.core.config import settings
from app.api.crud import crud_subscription


@pytest.fixture
def pilot(monkeypatch):
    monkeypatch.setattr(settings, "pilot_mode_enabled", True, raising=False)


def expire(db, user, days):
    sub = crud_subscription.get_active_subscription(db, user.id)
    sub.trial_end = utcnow() - timedelta(days=days)
    sub.current_period_end = sub.trial_end
    db.commit()


def test_pilot_trial_has_approved_limits_and_no_checkout(client, db, make_user, headers_for, pilot):
    owner = make_user("business_owner")
    headers = headers_for(owner)
    state = client.get("/subscriptions/access", headers=headers)
    assert state.status_code == 200
    assert state.json()["crm_mode"] == "full"
    assert state.json()["checkout_available"] is False
    usage = client.get("/subscriptions/usage", headers=headers).json()
    assert usage["crm"]["limit"] == 300
    assert usage["ai"]["daily_requests_limit"] == 50
    assert client.post("/subscriptions/checkout/create-session?plan_id=1", headers=headers).status_code == 503


def test_grace_allows_reads_but_never_mutations_exports_or_ai(client, db, make_user, headers_for, pilot):
    owner = make_user("business_owner")
    expire(db, owner, 1)
    headers = headers_for(owner)
    state = client.get("/subscriptions/access", headers=headers).json()
    assert state["crm_mode"] == "read_only"
    assert state["can_export"] is False and state["can_use_ai"] is False
    assert client.get("/clients/", headers=headers).status_code == 200
    assert client.post("/clients/", headers=headers, json={}).status_code == 402
    assert client.get("/clients/export/csv", headers=headers).status_code == 402
    assert client.post("/assistant/plan", headers=headers, json={}).status_code == 402


def test_after_grace_business_data_is_closed_but_account_and_billing_work(client, db, make_user, headers_for, pilot):
    owner = make_user("business_owner")
    expire(db, owner, 3)
    headers = headers_for(owner)
    assert client.get("/subscriptions/access", headers=headers).json()["crm_mode"] == "blocked"
    assert client.get("/clients/", headers=headers).status_code == 402
    assert client.get("/users/me", headers=headers).status_code == 200
    assert client.get("/subscriptions/trial/status", headers=headers).status_code == 200


def test_pilot_registration_requires_an_explicit_invitation(client, pilot):
    result = client.post("/users/", json={"full_name": "Pilot", "email": "uninvited@example.com", "phone": "+420123456789", "password": "Str0ng!Pass-word", "role": "business_owner"})
    assert result.status_code == 403


def test_pilot_never_uses_configured_live_notification_or_payment_providers(pilot, monkeypatch):
    from app.api.core.notification_transport import OutboundMessage, get_notification_transport
    from app.api.core.service_payment_provider import get_service_payment_provider
    monkeypatch.setattr(settings, "service_payment_provider", "sandbox")
    monkeypatch.setattr(settings, "service_payment_webhook_secret", "synthetic-sandbox-secret")
    monkeypatch.setattr(settings, "notification_transport", "live")
    assert get_service_payment_provider() is None
    result = get_notification_transport().send(OutboundMessage(channel="email", recipient="nobody@example.com", text="test"))
    assert result.status == "skipped" and result.error == "pilot_delivery_disabled"


def test_even_invited_email_cannot_be_claimed_without_google_verification(client, db, pilot, monkeypatch):
    from app.db.models.user import User
    monkeypatch.setattr(settings, "pilot_invited_emails", "invited@example.com")
    payload = {"full_name": "Pilot", "email": "invited@example.com", "phone": "+420123456789", "password": "Str0ng!Pass-word", "role": "business_owner"}
    response = client.post("/users/", json=payload)
    assert response.status_code == 403
    assert "pilot_google_signup_required" in response.text
    assert db.query(User).filter(User.email == payload["email"]).count() == 0


def test_guest_management_and_public_booking_cannot_bypass_expiry(client, db, make_user, headers_for, pilot):
    from app.api.core import booking_manage_access
    from app.db.models.public_booking import PublicBookingSettings
    from app.db.models.visit import Visit
    from tests.tenant_world import build_tenant
    tenant = build_tenant(client, make_user, headers_for, "Pilot", "+42071234")
    visit = db.get(Visit, tenant["visit"]["id"])
    raw, _ = booking_manage_access.issue_token(db, visit)
    db.add(PublicBookingSettings(business_id=tenant["business"]["id"], slug="pilot-salon", is_enabled=True))
    db.commit()
    expire(db, tenant["owner"], 1)
    assert client.get("/public/booking-management", headers={"X-Booking-Token": raw}).status_code == 200
    assert client.post("/public/booking-management/confirm", headers={"X-Booking-Token": raw}).status_code == 402
    assert client.get("/public/booking/pilot-salon").status_code == 402
    expire(db, tenant["owner"], 3)
    assert client.get("/public/booking-management", headers={"X-Booking-Token": raw}).status_code == 402


def test_visit_quota_counts_repeated_clients_and_update_does_not_spend_a_second_slot(client, db, make_user, headers_for, pilot):
    from app.api.core.crm_metering import count_monthly_visits
    from app.db.models.visit import Visit
    from tests.tenant_world import build_tenant
    tenant = build_tenant(client, make_user, headers_for, "Pilot", "+42071234")
    owner = tenant["owner"]
    start = utcnow().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    # One client has 299 additional visits; this is 300 units, not one active client.
    for index in range(299):
        moment = start + timedelta(minutes=index * 2)
        db.add(Visit(client_id=tenant["client"]["id"], specialist_id=owner.id,
                     business_id=tenant["business"]["id"], date_start=moment,
                     date_end=moment + timedelta(minutes=1), status="completed"))
    db.commit()
    assert count_monthly_visits(db, tenant["business"]["id"]) == 300
    next_start = start + timedelta(days=4)
    payload = {"client_id": tenant["client"]["id"], "specialist_id": owner.id,
               "date_start": next_start.isoformat(), "date_end": (next_start + timedelta(hours=1)).isoformat()}
    assert client.post("/visits/", json=payload, headers=tenant["headers"]).status_code == 402
    current = tenant["visit"]["id"]
    assert client.put(f"/visits/{current}", json={"note": "Updated"}, headers=tenant["headers"]).status_code == 200
    deleted = Visit(client_id=tenant["client"]["id"], specialist_id=owner.id,
                    business_id=tenant["business"]["id"], date_start=start + timedelta(days=6),
                    date_end=start + timedelta(days=6, hours=1), deleted_at=utcnow())
    db.add(deleted)
    db.commit()
    assert client.post(f"/visits/{deleted.id}/restore", headers=tenant["headers"]).status_code == 402
    assert client.put(f"/visits/{current}", json={"status": "cancelled"}, headers=tenant["headers"]).status_code == 200
    assert client.post("/visits/", json=payload, headers=tenant["headers"]).status_code == 200


def test_trial_grace_boundaries_use_server_time(client, db, make_user, headers_for, pilot, monkeypatch):
    from app.api.core import subscription_access
    owner = make_user("business_owner")
    sub = crud_subscription.get_active_subscription(db, owner.id)
    deadline = utcnow().replace(microsecond=0)
    sub.trial_end = deadline
    sub.current_period_end = deadline
    db.commit()
    monkeypatch.setattr(subscription_access, "utcnow", lambda: deadline + timedelta(seconds=1))
    state = subscription_access.get_subscription_access_state(db, owner.id)
    assert state["crm_mode"] == "read_only"
    monkeypatch.setattr(subscription_access, "utcnow", lambda: deadline + timedelta(days=3))
    assert subscription_access.get_subscription_access_state(db, owner.id)["crm_mode"] == "blocked"


def test_staff_uses_the_owners_expired_period(client, db, make_user, headers_for, pilot):
    from app.api.crud import crud_employee
    from app.db.models.business import Business
    from app.schemas.employee import EmployeeCreate
    owner = make_user("business_owner")
    business = Business(name="Shared pilot", business_type="salon")
    db.add(business)
    db.commit()
    owner.business_id = business.id
    staff = make_user("specialist")
    db.commit()
    crud_employee.create_employee(db, EmployeeCreate(employee_user_id=staff.id), owner.id)
    expire(db, owner, 3)
    # Staff's own signup trial cannot reopen its employer's closed CRM.
    assert client.get("/subscriptions/access", headers=headers_for(staff)).json()["crm_mode"] == "blocked"
    assert client.get("/clients/", headers=headers_for(staff)).status_code == 402


def test_crm_and_ai_can_coexist_and_free_ai_month_never_auto_renews(db, make_user, pilot):
    from app.api.core.subscription_access import get_subscription_access_state
    from app.schemas.subscription import SubscriptionCreate
    owner = make_user("business_owner")
    plan = crud_subscription.get_plan_by_code(db, "solo_basic")
    trial = crud_subscription.get_active_subscription(db, owner.id)
    trial.status = "expired"
    trial.current_period_end = utcnow() - timedelta(days=1)
    trial.trial_end = trial.current_period_end
    paid = crud_subscription.create_subscription(db, SubscriptionCreate(user_id=owner.id, plan_id=plan.id,
                  status="active", current_period_end=utcnow() + timedelta(days=40)))
    paid.offer_code = "crm_standard_czk"
    db.commit()
    state = get_subscription_access_state(db, owner.id)
    assert state["can_use_ai"] is True and state["ai_daily_limit"] == 50
    paid.created_at = utcnow() - timedelta(days=35)
    db.commit()
    assert get_subscription_access_state(db, owner.id)["can_use_ai"] is False
    ai = crud_subscription.create_subscription(db, SubscriptionCreate(user_id=owner.id, plan_id=plan.id,
                  status="active", current_period_end=utcnow() + timedelta(days=30)))
    ai.offer_code = "ai_business_czk"
    db.commit()
    state = get_subscription_access_state(db, owner.id)
    assert state["crm_mode"] == "full" and state["ai_daily_limit"] == 100


def test_concurrent_visits_share_the_last_monthly_slot(client, db, make_user, headers_for, pilot):
    import threading
    from fastapi.testclient import TestClient
    from app.main import app
    from app.db.models.visit import Visit
    from tests.tenant_world import build_tenant
    tenant = build_tenant(client, make_user, headers_for, "Pilot", "+42071234")
    start = utcnow().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    for index in range(298):
        moment = start + timedelta(minutes=index * 2)
        db.add(Visit(client_id=tenant["client"]["id"], specialist_id=tenant["owner"].id,
                     business_id=tenant["business"]["id"], date_start=moment,
                     date_end=moment + timedelta(minutes=1), status="completed"))
    db.commit()
    owner_id = tenant["owner"].id
    barrier = threading.Barrier(2)
    outcomes = []

    def attempt(day):
        moment = start + timedelta(days=day)
        payload = {"client_id": tenant["client"]["id"], "specialist_id": owner_id,
                   "date_start": moment.isoformat(), "date_end": (moment + timedelta(hours=1)).isoformat()}
        barrier.wait(timeout=5)
        with TestClient(app) as local_client:
            outcomes.append(local_client.post("/visits/", json=payload, headers=tenant["headers"]).status_code)

    threads = [threading.Thread(target=attempt, args=(day,)) for day in (4, 5)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=15)
    assert not any(thread.is_alive() for thread in threads)
    assert sorted(outcomes) == [200, 402]


def test_business_gate_never_matches_solo_owners_for_a_missing_business(db, make_user, pilot):
    from fastapi import HTTPException
    from app.api.core.subscription_access import enforce_business_access
    make_user("business_owner")
    # A solo owner with a live trial must not open a credential that has no salon behind it.
    with pytest.raises(HTTPException) as denied:
        enforce_business_access(db, None, write=False)
    assert denied.value.status_code == 402


def test_client_qr_history_closes_with_the_salon(client, db, make_user, headers_for, pilot):
    from app.api.crud import crud_client
    from tests.tenant_world import build_tenant
    tenant = build_tenant(client, make_user, headers_for, "Pilot", "+42071234")
    qr_data, pin = crud_client.generate_client_qr_code(db, tenant["client"]["id"])
    access = lambda: client.post("/clients/qr-code/access", json={"qr_code_data": qr_data, "password": pin})
    assert access().status_code == 200
    expire(db, tenant["owner"], 1)
    assert access().status_code == 200
    expire(db, tenant["owner"], 3)
    assert access().status_code == 402


def test_solo_owner_client_qr_history_closes_with_the_owner(client, db, make_user, headers_for, pilot):
    from app.api.crud import crud_client
    owner = make_user("specialist")
    created = client.post("/clients/", json={"full_name": "Solo QR", "contact_phone": "+420777000555"}, headers=headers_for(owner))
    assert created.status_code == 200, created.text
    qr_data, pin = crud_client.generate_client_qr_code(db, created.json()["id"])
    access = lambda: client.post("/clients/qr-code/access", json={"qr_code_data": qr_data, "password": pin})
    assert access().status_code == 200
    expire(db, owner, 3)
    assert access().status_code == 402
