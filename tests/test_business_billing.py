from datetime import timedelta
from types import SimpleNamespace

import pytest

from app.api.core.clock import utcnow
from app.api.core.config import settings
from app.api.crud import crud_employee, crud_subscription
from app.schemas.employee import EmployeeCreate


def hire(db, make_user, owner, role="specialist"):
    user = make_user(role)
    crud_employee.create_employee(db, EmployeeCreate(employee_user_id=user.id), owner.id)
    return user


def test_owner_gets_trial_and_manages_billing(client, db, make_user, headers_for, monkeypatch):
    import stripe

    owner = make_user("business_owner")
    sub = crud_subscription.get_active_subscription(db, owner.id)
    assert sub is not None and sub.is_trial
    # Existing admin plans cover teams; keep that trial rather than adding a tariff.
    assert sub.plan.code == "trial_admin"
    assert sub.trial_end - sub.trial_start == timedelta(days=5)
    headers = headers_for(owner)
    assert client.get("/subscriptions/me", headers=headers).json()["id"] == sub.id
    assert client.get("/subscriptions/history", headers=headers).json()[0]["id"] == sub.id
    assert client.get(f"/subscriptions/{sub.id}/transactions", headers=headers).status_code == 200
    monkeypatch.setattr(settings, "stripe_api_key", "test-key")
    monkeypatch.setattr(stripe.Customer, "list", lambda **kw: stripe.StripeObject.construct_from(
        {"data": [{"id": "cus_owner", "metadata": {"user_id": str(owner.id)}}]}, "test-key"))
    for target in (stripe.Subscription, stripe.checkout.Session):
        monkeypatch.setattr(target, "list", lambda **_: stripe.StripeObject.construct_from({"data": []}, "test-key"))
    captured = {}

    def checkout(**kw):
        captured.update(kw)
        return stripe.StripeObject.construct_from({"id": "cs_owner", "url": "https://example.com/checkout"}, "test-key")

    monkeypatch.setattr(stripe.checkout.Session, "create", checkout)
    paid_plan = crud_subscription.get_plan_by_code(db, "admin_up_to_3_employees")
    response = client.post(f"/subscriptions/checkout/create-session?plan_id={paid_plan.id}", headers=headers)
    assert response.status_code == 200
    assert captured["metadata"]["user_id"] == str(owner.id)
    assert captured["subscription_data"]["metadata"] == captured["metadata"]
    monkeypatch.setattr(stripe.checkout.Session, "retrieve", lambda _: SimpleNamespace(
        id="cs_owner", payment_status="paid", subscription="sub_owner", metadata={"user_id": str(owner.id)}))
    assert client.get("/subscriptions/checkout/session/cs_owner", headers=headers).status_code == 200
    other = make_user("business_owner")
    assert client.get("/subscriptions/checkout/session/cs_owner", headers=headers_for(other)).status_code == 403
    assert client.post(f"/subscriptions/{sub.id}/cancel", json={"cancel_at_period_end": True}, headers=headers).status_code == 200
    assert client.post(f"/subscriptions/{sub.id}/reactivate", json={}, headers=headers).status_code == 200
    assert client.get(f"/subscriptions/{sub.id}/transactions", headers=headers_for(other)).status_code == 403


@pytest.mark.parametrize("role", ["specialist", "employee"])
def test_staff_uses_owner_subscription_not_own(client, db, make_user, headers_for, role):
    owner = make_user("business_owner")
    staff = hire(db, make_user, owner, role)
    headers = headers_for(staff)
    assert crud_subscription.check_subscription_access(db, staff.id) == (True, None)
    assert not crud_subscription.check_subscription_access(db, staff.id, require_paid=True)[0]
    trial = client.get("/subscriptions/trial/status", headers=headers).json()
    assert trial["is_trial_active"] and trial["has_access"]
    # staff never buy a plan themselves
    assert client.post("/subscriptions/checkout/create-session?plan_id=1", headers=headers).status_code == 403
    owner_sub = crud_subscription.get_active_subscription(db, owner.id)
    # and never get to touch the owner's subscription
    for path in [f"/subscriptions/{owner_sub.id}/cancel", f"/subscriptions/{owner_sub.id}/reactivate"]:
        assert client.post(path, json={}, headers=headers).status_code == 403
    assert client.get(f"/subscriptions/{owner_sub.id}/transactions", headers=headers).status_code == 403
    if role == "employee":
        assert client.get("/subscriptions/me", headers=headers).status_code == 403
    else:
        me = client.get("/subscriptions/me", headers=headers).json()
        assert me is None or me["user_id"] == staff.id
        assert owner_sub.id not in [s["id"] for s in client.get("/subscriptions/history", headers=headers).json()]
    owner_sub.current_period_end = utcnow() - timedelta(days=1)
    owner_sub.trial_end = owner_sub.current_period_end
    db.commit()
    assert not crud_subscription.check_subscription_access(db, staff.id)[0]
    assert not client.get("/subscriptions/trial/status", headers=headers).json()["has_access"]
    # The specialist's old solo trial must never bypass the expired owner's plan.
    if role == "specialist":
        assert crud_subscription.get_active_subscription(db, staff.id) is not None


def test_hired_specialist_can_still_cancel_own_old_plan(client, db, make_user, headers_for):
    # someone who paid as a solo and then joined a salon must not be stuck paying
    owner = make_user("business_owner")
    staff = hire(db, make_user, owner)
    sub = crud_subscription.get_active_subscription(db, staff.id)
    headers = headers_for(staff)
    assert client.get("/subscriptions/me", headers=headers).json()["id"] == sub.id
    assert client.post(f"/subscriptions/{sub.id}/cancel", json={"cancel_at_period_end": True},
                       headers=headers).status_code == 200
    # but buying back in is the owner's job now
    assert client.post(f"/subscriptions/{sub.id}/reactivate", json={}, headers=headers).status_code == 403


def test_staff_from_create_with_user_get_no_trial(client, db, make_user, headers_for):
    # owner without a business yet: the new user has no business_id, still no solo trial
    owner = make_user("business_owner")
    r = client.post("/employees/create-with-user", json={"full_name": "New Staff", "email": "new.staff@example.com"},
                    headers=headers_for(owner))
    assert r.status_code == 200, r.text
    staff_id = r.json()["employee_user_id"]
    assert crud_subscription.get_active_subscription(db, staff_id) is None
    assert crud_subscription.check_subscription_access(db, staff_id) == (True, None)


def test_fired_employee_has_no_access(client, db, make_user, headers_for):
    owner = make_user("business_owner")
    staff = hire(db, make_user, owner, "employee")
    crud_employee.fire_employee(db, crud_employee.get_employee_by_user_id(db, staff.id, owner.id))
    assert crud_subscription.check_subscription_access(db, staff.id)[0] is False
    trial = client.get("/subscriptions/trial/status", headers=headers_for(staff))
    assert trial.status_code == 200
    assert trial.json() == {"is_trial_active": False, "is_trial": False, "trial_start": None,
                            "trial_end": None, "has_access": False}


def test_staff_registration_does_not_create_trial(client, db, make_user, headers_for):
    owner = make_user("business_owner")
    response = client.post("/businesses/", json={"name": "Salon", "business_type": "salon"}, headers=headers_for(owner))
    assert response.status_code == 200
    staff = make_user("specialist", business_id=response.json()["id"])
    assert crud_subscription.get_active_subscription(db, staff.id) is None
    assert crud_subscription.create_trial_subscription(db, staff.id, staff.role) is None


@pytest.mark.parametrize("resource", ["work", "blocks"])
@pytest.mark.parametrize("role", ["specialist", "employee"])
def test_owner_manages_only_active_staff_schedules(client, db, make_user, headers_for, resource, role):
    owner = make_user("business_owner")
    staff = hire(db, make_user, owner, role)
    outsider = make_user("specialist")
    other = make_user("business_owner")
    headers = headers_for(owner)
    payload = {"weekday": 0, "start_time": "09:00", "end_time": "17:00"} if resource == "work" else {
        "start": "2030-01-01T09:00:00Z", "end": "2030-01-01T10:00:00Z", "reason": "Meeting"}
    response = client.post(f"/schedules/{resource}", json={**payload, "specialist_id": staff.id}, headers=headers)
    assert response.status_code == 200
    object_id = response.json()["id"]
    update = {"end_time": "18:00"} if resource == "work" else {"reason": "Changed"}
    assert client.put(f"/schedules/{resource}/{object_id}", json=update, headers=headers).status_code == 200
    assert len(client.get(f"/schedules/{resource}?specialist_id={staff.id}", headers=headers).json()) == 1
    assert client.get(f"/schedules/{resource}?specialist_id={staff.id}", headers=headers_for(other)).status_code == 403
    assert client.put(f"/schedules/{resource}/{object_id}", json=update, headers=headers_for(other)).status_code == 403
    assert client.post(f"/schedules/{resource}", json={**payload, "specialist_id": outsider.id}, headers=headers).status_code == 403
    employee = crud_employee.get_employee_by_user_id(db, staff.id, owner.id)
    crud_employee.fire_employee(db, employee)
    assert client.get(f"/schedules/{resource}?specialist_id={staff.id}", headers=headers).status_code == 403
    assert client.put(f"/schedules/{resource}/{object_id}", json=update, headers=headers).status_code == 403


def test_staff_uses_paid_owner_plan_even_without_employer_field(client, db, make_user, headers_for):
    owner = make_user("business_owner")
    staff = hire(db, make_user, owner)
    staff.employer_id = None
    sub = crud_subscription.get_active_subscription(db, owner.id)
    sub.is_trial = False
    sub.status = "pending_cancellation"
    db.commit()
    assert crud_subscription.check_subscription_access(db, staff.id, require_paid=True) == (True, None)
    assert client.get("/subscriptions/trial/status", headers=headers_for(staff)).json()["has_access"]
    # /me is the staff member's own (old solo) plan, never the owner's
    assert client.get("/subscriptions/me", headers=headers_for(staff)).json()["user_id"] == staff.id
    sub.current_period_end = utcnow() - timedelta(seconds=1)
    db.commit()
    assert not crud_subscription.check_subscription_access(db, staff.id)[0]
    assert not client.get("/subscriptions/trial/status", headers=headers_for(staff)).json()["has_access"]


def test_solo_specialist_keeps_own_trial(client, db, make_user, headers_for):
    solo = make_user("specialist")
    sub = crud_subscription.get_active_subscription(db, solo.id)
    assert sub.plan.code == "trial_specialist"
    assert sub.trial_end - sub.trial_start == timedelta(days=14)
    assert crud_subscription.check_subscription_access(db, solo.id) == (True, None)
    assert client.get("/subscriptions/me", headers=headers_for(solo)).status_code == 200
    assert client.get("/subscriptions/history", headers=headers_for(solo)).json()[0]["id"] == sub.id


def test_schedule_rights_for_staff_admin_and_solo(client, db, make_user, headers_for):
    owner = make_user("business_owner")
    staff = hire(db, make_user, owner)
    colleague = hire(db, make_user, owner)
    solo = make_user("specialist")
    admin = make_user("admin")
    body = {"weekday": 1, "start_time": "09:00", "end_time": "17:00"}
    # staff run their own schedule, not a colleague's
    assert client.post("/schedules/work", json={**body, "specialist_id": staff.id},
                       headers=headers_for(staff)).status_code == 200
    assert client.post("/schedules/work", json={**body, "specialist_id": colleague.id},
                       headers=headers_for(staff)).status_code == 403
    assert client.get(f"/schedules/work?specialist_id={colleague.id}", headers=headers_for(staff)).status_code == 403
    # a solo specialist only has themselves
    assert client.post("/schedules/work", json={**body, "specialist_id": solo.id},
                       headers=headers_for(solo)).status_code == 200
    assert client.get(f"/schedules/work?specialist_id={staff.id}", headers=headers_for(solo)).status_code == 403
    # and can't touch the owner's people either way round
    assert client.get(f"/schedules/work?specialist_id={solo.id}", headers=headers_for(owner)).status_code == 403
    # platform admin keeps the old reach
    assert client.get(f"/schedules/work?specialist_id={staff.id}", headers=headers_for(admin)).status_code == 200
