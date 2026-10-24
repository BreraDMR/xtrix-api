"""An uncertain Gmail acceptance needs reconciliation, not an automatic duplicate."""
from datetime import timedelta

import pytest

from app.api.core.clock import utcnow
from app.api.core.notification_outbox import enqueue_notification
from app.api.core.notification_transport import DeliveryResult
from app.api.core.notification_worker import run_once
from app.db.models.notification_outbox import NotificationOutbox


@pytest.mark.parametrize('error, status', [('gmail_delivery_uncertain', 'failed'), ('gmail_http:429', 'pending')])
def test_gmail_worker_keeps_uncertain_delivery_out_of_retry_queue(db, error, status):
    now = utcnow()
    row = enqueue_notification(db, event_key='gmail-policy', kind='password_reset', recipient='test@example.com',
                               business_id=None, channel='email', payload={}, scheduled_at=now)
    db.commit()
    calls = []
    def send(message):
        calls.append(message.id)
        return DeliveryResult('failed', 'gmail', error=error)
    run_once(db, send, now=now)
    db.expire_all()
    saved = db.get(NotificationOutbox, row.id)
    assert saved.status == status
    assert saved.attempts == 1
    result = run_once(db, send, now=now + timedelta(minutes=6))
    if status == 'failed':
        assert result['claimed'] == 0
        assert calls == [row.id]
    else:
        assert result['claimed'] == 1
        assert calls == [row.id, row.id]
