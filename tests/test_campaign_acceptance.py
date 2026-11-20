"""CAMPAIGN-11: withdrawal after queueing, schedule and competing workers."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier, Lock

import pytest

from app.api.core import campaign_service, notification_worker
from app.api.core.clock import utcnow
from app.api.core.notification_transport import DeliveryResult
from app.db.session import SessionLocal
from tests import test_campaigns as campaigns

pytest_plugins = ["tests.test_campaigns"]


def _queued(client, db, world, count=1, **fields):
    consents = []
    for number in range(count):
        row = campaigns._add_client(client, world, f"Guest {number}", f"guest{number}@example.com")
        campaigns._age_relation(db, world["business"]["id"], row["id"], 200)
        consents.append(campaigns._consent(db, world, row))
    campaign = campaigns._create(client, world, **fields)
    campaigns._ok(campaigns._confirm(client, world, campaign["id"]), "confirm")
    campaigns._ok(client.post(f"/businesses/me/campaigns/{campaign['id']}/send",
                             headers=world["headers"]), "send")
    return campaign, consents


@pytest.mark.parametrize("source", ["owner", "link"])
def test_withdrawal_after_queueing_wins_before_delivery(client, db, world, source):
    campaign, consents = _queued(client, db, world, count=2)
    consent = consents[0]
    if source == "owner":
        response = client.post(
            f"/businesses/me/clients/{consent.client_id}/marketing-consent/revoke",
            headers=world["headers"],
        )
    else:
        response = client.post("/public/campaigns/unsubscribe", json={
            "token": campaign_service.unsubscribe_token(consent.id, campaign["id"]),
        })
    campaigns._ok(response, "withdraw consent")
    delivered = []
    result = notification_worker.run_once(db, lambda row: (
        delivered.append(row.recipient), DeliveryResult("captured", "capture"),
    )[1])
    assert delivered == ["guest1@example.com"]
    assert result["cancelled"] == 1 and result["captured"] == 1
    rows = campaigns._outbox(db, campaign["id"])
    assert rows[0].status == "cancelled" and rows[0].last_error == "unsubscribed"
    stats = campaigns._ok(client.get(f"/businesses/me/campaigns/{campaign['id']}/stats",
                                    headers=world["headers"]), "stats")
    assert stats["cancelled"] == 1 and stats["captured"] == 1
    assert stats["sent"] == stats["delivered"] == 0


def test_scheduled_campaign_waits_and_two_workers_capture_each_recipient_once(client, db, world):
    due = utcnow() + timedelta(hours=2)
    campaign, _ = _queued(client, db, world, count=4, scheduled_at=due.isoformat() + "Z")
    sent = []
    lock = Lock()
    barrier = Barrier(2)

    def capture(row):
        with lock:
            sent.append(row.recipient)
        return DeliveryResult("captured", "capture")

    before = notification_worker.run_once(db, capture, now=due - timedelta(seconds=1))
    assert before["claimed"] == 0 and sent == []

    def run():
        with SessionLocal() as session:
            barrier.wait(timeout=10)
            return notification_worker.run_once(session, capture, now=due, limit=2)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: run(), range(2)))
    assert sum(result["captured"] for result in results) == 4
    assert sorted(sent) == [f"guest{n}@example.com" for n in range(4)]
    assert notification_worker.run_once(db, capture, now=due)["claimed"] == 0
    assert len(campaigns._outbox(db, campaign["id"])) == 4
