"""A reminder already claimed by the worker must still notice a cancel or a move."""
from datetime import timedelta

import pytest

from app.api.core import notification_events, notification_scheduler, notification_worker
from app.api.core.notification_transport import DeliveryResult
from app.db.models.notification_outbox import NotificationOutbox
from tests.test_waitlist import _cancel, _visit, salon  # noqa: F401  (salon is a fixture)


def _move(client, salon, visit):  # noqa: F811
    start = salon["start"] + timedelta(days=1)
    return client.put(
        f"/visits/{visit['id']}",
        json={"date_start": start.isoformat(), "date_end": (start + timedelta(hours=1)).isoformat()},
        headers=salon["headers"],
    )


@pytest.mark.parametrize("change", ["cancel", "move"])
def test_claimed_reminder_is_dropped_when_the_visit_changes(client, db, salon, monkeypatch, change):  # noqa: F811
    visit = _visit(client, salon)
    notification_scheduler.sync_reminders(db)
    row = db.query(NotificationOutbox).filter_by(
        visit_id=visit["id"], kind=notification_events.KIND_REMINDER, status="pending"
    ).one()
    due = row.scheduled_at + timedelta(minutes=1)

    real_claim = notification_worker.claim_leases

    def claim_then_change(*args, **kwargs):
        # the owner's change lands right after the worker took the row
        ids = real_claim(*args, **kwargs)
        response = _cancel(client, salon, visit["id"]) if change == "cancel" else _move(client, salon, visit)
        assert response.status_code == 200, response.text
        return ids

    monkeypatch.setattr(notification_worker, "claim_leases", claim_then_change)
    sent = []
    notification_worker.run_once(
        db, lambda message: sent.append(message.id) or DeliveryResult("sent", "test", provider_message_id="m-1"),
        now=due,
    )
    assert row.id not in sent
    db.expire_all()
    assert db.get(NotificationOutbox, row.id).status == "cancelled"


def test_worker_that_lost_its_lease_does_not_send_again(client, db, salon, monkeypatch):  # noqa: F811
    visit = _visit(client, salon)
    notification_scheduler.sync_reminders(db)
    row = db.query(NotificationOutbox).filter_by(
        visit_id=visit["id"], kind=notification_events.KIND_REMINDER, status="pending"
    ).one()
    due = row.scheduled_at + timedelta(minutes=1)
    sent = []

    def send(message):
        sent.append(message.id)
        return DeliveryResult("sent", "smtp")  # SMTP gives no provider id to dedupe on

    real_claim = notification_worker.claim_leases

    def stalled_claim(*args, **kwargs):
        ids = real_claim(*args, **kwargs)
        # A disconnected worker loses its session lock; the next worker may recover its lease.
        kwargs["_locks"].close()
        monkeypatch.setattr(notification_worker, "claim_leases", real_claim)
        notification_worker.run_once(db, send, now=due + notification_worker.LEASE + timedelta(minutes=1))
        return ids

    monkeypatch.setattr(notification_worker, "claim_leases", stalled_claim)
    notification_worker.run_once(db, send, now=due)
    assert sent == [row.id]
    db.expire_all()
    assert db.get(NotificationOutbox, row.id).status == "sent"
