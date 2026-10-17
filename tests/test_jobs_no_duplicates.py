from datetime import date, timedelta

import pytest

from app.api.core import notify, notification_tasks
from app.api.core.clock import utcnow
from tests.tenant_world import build_tenant


class Outbox(list):
    up = True  # flip to False to pretend SMTP is down


@pytest.fixture
def outbox(monkeypatch):
    sent = Outbox()
    monkeypatch.setattr(notify, "_send_email", lambda to, subject, body: sent.up and (sent.append(subject) or True))
    return sent


def _visit(client, a, start, **extra):
    body = {"client_id": a["client"]["id"], "specialist_id": a["owner"].id,
            "date_start": start.isoformat(), "date_end": (start + timedelta(hours=1)).isoformat(), "price": 100}
    r = client.post("/visits/", json=body, headers=a["headers"])
    assert r.status_code == 200, r.text
    if extra:
        r = client.put(f"/visits/{r.json()['id']}", json=extra, headers=a["headers"])
        assert r.status_code == 200, r.text
    return r.json()


def test_reminder_goes_once_and_again_after_a_move(client, make_user, headers_for, outbox):
    a = build_tenant(client, make_user, headers_for, "Alpha", "+42070011")
    soon = (utcnow() + timedelta(hours=5)).replace(microsecond=0)
    v = _visit(client, a, soon)

    assert notification_tasks.send_automatic_reminders("email") == 1
    assert notification_tasks.send_automatic_reminders("email") == 0
    moved = soon + timedelta(hours=2)
    client.put(f"/visits/{v['id']}", headers=a["headers"],
               json={"date_start": moved.isoformat(), "date_end": (moved + timedelta(hours=1)).isoformat()})
    assert notification_tasks.send_automatic_reminders("email") == 1


def test_failed_send_is_retried_next_run(client, make_user, headers_for, outbox):
    a = build_tenant(client, make_user, headers_for, "Alpha", "+42070011")
    _visit(client, a, (utcnow() + timedelta(hours=5)).replace(microsecond=0))
    outbox.up = False
    assert notification_tasks.send_automatic_reminders("email") == 0
    outbox.up = True
    assert notification_tasks.send_automatic_reminders("email") == 1


def test_followup_and_birthday_go_once(client, make_user, headers_for, outbox):
    a = build_tenant(client, make_user, headers_for, "Alpha", "+42070011")
    done = (utcnow() - timedelta(hours=25, minutes=30)).replace(microsecond=0)
    _visit(client, a, done, status="completed", payment_method="cash")
    client.put(f"/clients/{a['client']['id']}", headers=a["headers"],
               json={"birth_date": date.today().replace(year=1992).isoformat()})

    assert notification_tasks.send_visit_followups("email") == 1
    assert notification_tasks.send_visit_followups("email") == 0
    assert notification_tasks.send_birthday_greetings("email") == 1
    assert notification_tasks.send_birthday_greetings("email") == 0


def test_staff_jobs_go_once(client, db, make_user, headers_for, outbox):
    from app.api.core.product_notifications import send_low_stock_notifications

    a = build_tenant(client, make_user, headers_for, "Alpha", "+42070011")
    client.post("/products/", headers=a["headers"],
                json={"name": "Alpha Wax", "business_id": a["business"]["id"], "quantity": 1, "min_quantity": 5})
    assert send_low_stock_notifications(db) == 1
    assert send_low_stock_notifications(db) == 0


def test_29_february_birthday():
    from app.api.core.loyalty_logic import _birthday_in

    assert _birthday_in(2027, date(1992, 2, 29)) == date(2027, 2, 28)
    assert _birthday_in(2028, date(1992, 2, 29)) == date(2028, 2, 29)
