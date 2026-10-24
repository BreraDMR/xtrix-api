from app.api.core import notification_worker
from app.api.core.notification_transport import DeliveryResult
from app.db.models.notification_outbox import NotificationOutbox

def test_active_provider_send_is_not_reclaimed_after_lease_expiry(db):
    from datetime import timedelta
    from app.api.core.clock import utcnow
    from app.api.core.notification_outbox import enqueue_notification
    from app.db.session import SessionLocal
    now = utcnow()
    row = enqueue_notification(db,event_key='review-slow-send',kind='password_reset',recipient='slow@example.com',business_id=None,channel='email',payload={},scheduled_at=now)
    db.commit()
    message_id=row.id
    sends=[]
    second_stats=[]
    def fast_send(message):
        sends.append(('second',message.id))
        return DeliveryResult('sent','local-probe','accepted-second')
    def slow_send(message):
        sends.append(('first',message.id))
        with SessionLocal() as second:
            second_stats.append(notification_worker.run_once(second,fast_send,now=now+timedelta(minutes=6)))
        return DeliveryResult('sent','local-probe','accepted-first')
    first_stats=notification_worker.run_once(db,slow_send,now=now)
    db.expire_all()
    saved=db.get(NotificationOutbox,message_id)
    print({'send_calls':sends,'first':first_stats,'second':second_stats,'attempts':saved.attempts,'provider_id':saved.provider_message_id})
    assert sends == [('first',message_id)]
    assert second_stats[0]['claimed'] == 0
    assert saved.status == 'sent' and saved.provider_message_id == 'accepted-first'
    assert saved.attempts == 1


def test_provider_acceptance_is_recorded_when_cancel_arrives_during_send(db):
    from app.api.core.clock import utcnow
    from app.api.core.notification_outbox import enqueue_notification
    from app.db.session import SessionLocal
    now = utcnow()
    row = enqueue_notification(db, event_key='accepted-during-cancel', kind='password_reset', recipient='test@example.com',
                               business_id=None, channel='email', payload={}, scheduled_at=now)
    db.commit()
    message_id = row.id
    def send(message):
        with SessionLocal() as other:
            cancelled = other.get(NotificationOutbox, message.id)
            cancelled.status = 'cancelled'
            other.commit()
        return DeliveryResult('sent', 'local-probe', 'accepted-before-cancel')
    result = notification_worker.run_once(db, send, now=now)
    db.expire_all()
    saved = db.get(NotificationOutbox, message_id)
    assert result['sent'] == 1 and result['lost'] == 0
    assert saved.status == 'sent'
    assert saved.provider_message_id == 'accepted-before-cancel'
