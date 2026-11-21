"""The long-running worker (python -m app.worker) and the try-right-away path for urgent mail."""
import signal
from datetime import datetime, timedelta

import pytest

from app import worker as worker_module
from app.api.core import notification_templates, notification_transport, notification_worker
from app.api.core.clock import utcnow
from app.api.core.config import settings
from app.api.core.notification_outbox import enqueue_notification
from app.api.core.notification_transport import DeliveryResult
from app.db.models.notification_outbox import NotificationOutbox
from tests.test_public_booking_acceptance import _body, _local_utc, _ok, _salon


class Clock:
    def __init__(self, now):
        self.now = now

    def __call__(self):
        return self.now


def _worker(clock, deliver=None, **kwargs):
    ran = []
    w = worker_module.Worker(
        batch_size=kwargs.pop("batch_size", 10), poll_seconds=5, max_backoff_seconds=40,
        jobs_every_seconds=300, daily_hour=8,
        frequent={"often": lambda: ran.append("often")}, daily={"daily": lambda: ran.append("daily")},
        deliver=deliver or (lambda limit: {"claimed": 0}), clock=clock, **kwargs,
    )
    return w, ran


def test_full_batch_goes_again_at_once_and_a_short_one_waits():
    w, _ = _worker(Clock(datetime(2031, 5, 1, 9)), deliver=lambda limit: {"claimed": limit})
    assert w.tick() == 0
    w.deliver = lambda limit: {"claimed": 3}
    assert w.tick() == 5


def test_failures_back_off_up_to_the_cap_and_reset_after_success():
    calls = {"fail": True}

    def deliver(limit):
        if calls["fail"]:
            raise RuntimeError("db is down")
        return {"claimed": 0}

    w, _ = _worker(Clock(datetime(2031, 5, 1, 9)), deliver=deliver)
    assert [w.tick() for _ in range(5)] == [10, 20, 40, 40, 40]
    calls["fail"] = False
    assert w.tick() == 5 and w.failures == 0


def test_frequent_jobs_follow_their_interval_and_daily_ones_go_once_a_day_after_the_hour():
    clock = Clock(datetime(2031, 5, 1, 7, 0))
    w, ran = _worker(clock)
    w.tick()
    assert ran == ["often"]
    clock.now += timedelta(minutes=2)
    w.tick()
    assert ran == ["often"]
    clock.now = datetime(2031, 5, 1, 8, 1)
    w.tick()
    assert ran == ["often", "often", "daily"]
    clock.now += timedelta(minutes=6)
    w.tick()
    assert ran.count("daily") == 1
    clock.now = datetime(2031, 5, 2, 8, 0)
    w.tick()
    assert ran.count("daily") == 2


def test_a_broken_job_does_not_stop_delivery():
    delivered = []
    w, _ = _worker(Clock(datetime(2031, 5, 1, 9)), deliver=lambda limit: delivered.append(limit) or {"claimed": 0})
    w.frequent = {"boom": lambda: 1 / 0}
    assert w.tick() == 5
    assert delivered == [10]


def test_sigterm_finishes_the_round_and_stops():
    w, _ = _worker(Clock(datetime(2031, 5, 1, 9)))
    previous = signal.getsignal(signal.SIGTERM), signal.getsignal(signal.SIGINT)
    try:
        w.install_signal_handlers()
        handler = signal.getsignal(signal.SIGTERM)
        rounds = []

        def deliver(limit):
            rounds.append(limit)
            handler(signal.SIGTERM, None)
            return {"claimed": limit}  # a full batch would normally loop straight on

        w.deliver = deliver
        w.run()
    finally:
        signal.signal(signal.SIGTERM, previous[0])
        signal.signal(signal.SIGINT, previous[1])
    assert rounds == [10]


def test_worker_drains_the_real_outbox_in_batches(db, monkeypatch):
    sent = []
    monkeypatch.setattr(notification_templates, "send_outbox_message",
                        lambda _db, row: sent.append(row.id) or DeliveryResult("sent", "test", f"m-{row.id}"))
    for n in range(5):
        enqueue_notification(db, event_key=f"worker-test:{n}", kind="booking_created",
                             recipient=f"guest{n}@example.com", scheduled_at=utcnow() - timedelta(minutes=1))
    enqueue_notification(db, event_key="worker-test:later", kind="booking_created",
                         recipient="later@example.com", scheduled_at=utcnow() + timedelta(hours=1))
    db.commit()

    assert worker_module.deliver_batch(3)["claimed"] == 3
    assert worker_module.deliver_batch(3)["claimed"] == 2
    assert worker_module.deliver_batch(3)["claimed"] == 0
    assert len(sent) == len(set(sent)) == 5
    db.expire_all()
    assert db.query(NotificationOutbox).filter_by(event_key="worker-test:later").one().status == "pending"


def test_from_env_reads_sane_numbers(monkeypatch):
    monkeypatch.setenv("WORKER_BATCH_SIZE", "0")
    monkeypatch.setenv("WORKER_POLL_SECONDS", "nope")
    monkeypatch.setenv("JOBS_EVERY_MINUTES", "2")
    w = worker_module.Worker.from_env()
    assert (w.batch_size, w.poll_seconds, w.jobs_every_seconds) == (1, 5, 120)


@pytest.fixture
def capture(monkeypatch):
    monkeypatch.setattr(settings, "smtp_host", "smtp.invalid")
    monkeypatch.setattr(settings, "smtp_from", "notifications@example.com")
    monkeypatch.setattr(settings, "notification_transport", "capture")
    monkeypatch.setattr(notification_transport, "_transport", None)
    transport = notification_transport.get_notification_transport()
    transport.clear()
    return transport


def test_password_reset_is_attempted_right_away(client, db, make_user, capture):
    make_user("business_owner", email="hurry@example.com")
    assert client.post("/auth/password-reset/request", json={"email": "hurry@example.com"}).status_code == 200
    db.expire_all()
    row = db.query(NotificationOutbox).filter(NotificationOutbox.kind == "password_reset").one()
    assert row.status == "captured"
    assert [m.recipient for m in capture.messages()] == ["hurry@example.com"]


def test_password_reset_waits_for_the_worker_when_turned_off(client, db, make_user, capture, monkeypatch):
    monkeypatch.setattr(settings, "notification_immediate_delivery", False)
    make_user("business_owner", email="calm@example.com")
    client.post("/auth/password-reset/request", json={"email": "calm@example.com"})
    db.expire_all()
    assert db.query(NotificationOutbox).filter(NotificationOutbox.kind == "password_reset").one().status == "pending"
    assert capture.messages() == []


def test_public_booking_confirmation_is_attempted_right_away(client, db, make_user, headers_for, capture):
    salon = _salon(client, make_user, headers_for, "Hasty")
    _ok(client.put("/businesses/me/notification-settings", json={"is_enabled": True}, headers=salon["headers"]),
        "enable")
    start, _day = _local_utc(days_ahead=5)
    _ok(client.post(f"/public/booking/{salon['slug']}/bookings", json=_body(salon, start, "hasty-key-0001")),
        "booking")
    db.expire_all()
    row = db.query(NotificationOutbox).filter(NotificationOutbox.kind == "booking_created").one()
    assert row.status == "captured"
    # the reminder is for later and stays where it is
    assert all(r.status == "pending" for r in db.query(NotificationOutbox).filter(
        NotificationOutbox.kind == "booking_reminder"))


def test_deliver_now_never_touches_rows_it_was_not_asked_about(db, monkeypatch):
    sent = []
    monkeypatch.setattr(notification_templates, "send_outbox_message",
                        lambda _db, row: sent.append(row.event_key) or DeliveryResult("sent", "test", "m"))
    for key in ("mine", "other"):
        enqueue_notification(db, event_key=key, kind="booking_created", recipient=f"{key}@example.com")
    db.commit()
    assert notification_worker.deliver_now(db, ["mine"])["claimed"] == 1
    assert sent == ["mine"]
