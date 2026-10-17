from datetime import timedelta

import pytest

from app.api.core import notify
from app.api.core.clock import utcnow
from tests.tenant_world import build_tenant


@pytest.fixture
def outbox(monkeypatch):
    sent = []
    monkeypatch.setattr(notify, "_send_email", lambda to, subject, body: sent.append((to, subject, body)) or True)
    monkeypatch.setattr(notify, "_send_sms", lambda phone, body: True)
    return sent


def _low_product(client, a):
    r = client.post("/products/", headers=a["headers"],
                    json={"name": "Alpha Wax", "business_id": a["business"]["id"], "quantity": 1, "min_quantity": 5})
    assert r.status_code == 200, r.text


def test_low_stock_mail_in_owner_language_to_notification_email(client, db, make_user, headers_for, outbox):
    from app.api.core.product_notifications import send_low_stock_notifications

    a = build_tenant(client, make_user, headers_for, "Alpha", "+42070011")
    _low_product(client, a)
    r = client.put("/user-preferences/me", headers=a["headers"], json={"language": "cz", "notification_email": "boss@example.com"})
    assert r.status_code == 200, r.text

    assert send_low_stock_notifications(db) == 1
    to, subject, body = outbox[0]
    assert to == "boss@example.com"
    assert subject.startswith("⚠️ Docházející zásoby") and "Alpha Wax" in body


def test_email_notifications_off_means_no_mail(client, db, make_user, headers_for, outbox):
    from app.api.core.product_notifications import send_low_stock_notifications

    a = build_tenant(client, make_user, headers_for, "Alpha", "+42070011")
    _low_product(client, a)
    client.put("/user-preferences/me", headers=a["headers"], json={"email_notifications": False})

    assert send_low_stock_notifications(db) == 0 and outbox == []


def test_subscription_expiry_in_user_language(client, db, make_user, headers_for, outbox):
    from app.api.core.subscription_notifications import send_subscription_expiry_notifications
    from app.api.crud import crud_subscription
    from app.schemas.subscription import SubscriptionCreate

    user = make_user("specialist")
    client.put("/user-preferences/me", headers=headers_for(user), json={"language": "en"})
    plan = crud_subscription.list_plans(db)[0]
    crud_subscription.create_subscription(db, SubscriptionCreate(
        user_id=user.id, plan_id=plan.id, provider="manual", status="active",
        current_period_end=utcnow() + timedelta(days=3, hours=1)))

    assert send_subscription_expiry_notifications(db, days_before=3) == 1
    assert outbox[0][1] == "Your subscription expires in 3 days"
