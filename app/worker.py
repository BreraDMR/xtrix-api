"""Long-running worker: drains the notification outbox all the time and runs the scheduled jobs.

    python -m app.worker

The outbox is claimed with SELECT ... FOR UPDATE SKIP LOCKED under a lease (see
notification_worker), so several workers, the old cron endpoint and the request-time
"send right away" path can all run at once without sending a message twice.

Each loop round:
  * delivers one batch; a full batch means more is waiting, so the next round starts at once,
  * every JOBS_EVERY_MINUTES runs the frequent jobs (reminder sweep, freed slots, visitor mail...),
  * once a UTC day, from JOBS_DAILY_HOUR on, runs the daily ones (birthdays, subscriptions...).
A failing round backs off (poll interval doubling up to a cap) instead of hammering the database.
SIGTERM/SIGINT finish the current batch and stop.

Env: WORKER_BATCH_SIZE (50), WORKER_POLL_SECONDS (5), WORKER_MAX_BACKOFF_SECONDS (300),
JOBS_EVERY_MINUTES (5 here), JOBS_DAILY_HOUR (8), JOBS_CHANNEL (email).
"""
import os
import signal
import threading
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Callable, Optional

from app.api.core import finance_tasks, import_service, notification_scheduler, notification_tasks
from app.api.core import notification_templates, notification_worker, released_slot_queue
from app.api.core import subscription_tasks, visitor_proof_mail
from app.api.core.clock import utcnow
from app.api.core.logger import logger
from app.db.session import SessionLocal

CHANNEL = os.environ.get("JOBS_CHANNEL", "email")


def _sync_reminders() -> dict:
    db = SessionLocal()
    try:
        return notification_scheduler.sync_reminders(db)
    finally:
        db.close()


# outbox delivery itself is not here: the worker loop drains it continuously
FREQUENT: dict[str, Callable[[], object]] = {
    "import_sources": import_service.purge_expired_sources,
    "visitor_mail": visitor_proof_mail.run_scheduled,
    # before the reminder sweep, so waitlist offers from freed slots go out in the same round
    "released_slots": released_slot_queue.run_scheduled,
    "reminder_sync": _sync_reminders,
    "followups": lambda: notification_tasks.send_visit_followups(CHANNEL),
}
DAILY: dict[str, Callable[[], object]] = {
    "birthdays": lambda: notification_tasks.send_birthday_greetings(CHANNEL),
    "subscriptions": subscription_tasks.run_all_subscription_tasks,
    "low_stock": subscription_tasks.process_product_notifications,
    "finance_archive": finance_tasks.check_and_run_monthly_archive,
}


def _env_int(name: str, default: int) -> int:
    try:
        return max(0, int(os.environ.get(name, default)))
    except ValueError:
        return default


def deliver_batch(limit: int) -> dict:
    db = SessionLocal()
    try:
        return notification_worker.run_once(
            db, lambda row: notification_templates.send_outbox_message(db, row), limit=limit
        )
    finally:
        db.close()


def run_jobs(jobs: dict[str, Callable[[], object]]) -> bool:
    ok = True
    for name, job in jobs.items():
        try:
            logger.info(f"worker job {name}: {job()}")
        except Exception as e:
            # one broken job must not stop the others or the worker
            ok = False
            logger.error(f"worker job {name} failed: {e}", exc_info=True)
    return ok


@dataclass
class Worker:
    batch_size: int = 50
    poll_seconds: float = 5
    max_backoff_seconds: float = 300
    jobs_every_seconds: float = 300
    daily_hour: int = 8
    frequent: dict = field(default_factory=lambda: dict(FREQUENT))
    daily: dict = field(default_factory=lambda: dict(DAILY))
    deliver: Callable[[int], dict] = deliver_batch
    clock: Callable[[], datetime] = utcnow
    stop: threading.Event = field(default_factory=threading.Event)
    failures: int = 0
    last_frequent: Optional[datetime] = None
    last_daily: Optional[date] = None

    @classmethod
    def from_env(cls) -> "Worker":
        return cls(
            batch_size=max(1, _env_int("WORKER_BATCH_SIZE", 50)),
            poll_seconds=max(1, _env_int("WORKER_POLL_SECONDS", 5)),
            max_backoff_seconds=max(1, _env_int("WORKER_MAX_BACKOFF_SECONDS", 300)),
            jobs_every_seconds=_env_int("JOBS_EVERY_MINUTES", 5) * 60,
            daily_hour=_env_int("JOBS_DAILY_HOUR", 8),
        )

    def _backoff(self) -> float:
        return min(self.poll_seconds * (2 ** self.failures), self.max_backoff_seconds)

    def tick(self) -> float:
        """One round. Returns how long to wait before the next one."""
        now = self.clock()
        try:
            if self.last_frequent is None or (now - self.last_frequent).total_seconds() >= self.jobs_every_seconds:
                self.last_frequent = now
                run_jobs(self.frequent)
            # birthday wishes shouldn't arrive at 3 a.m.; the jobs themselves are idempotent anyway
            if now.hour >= self.daily_hour and self.last_daily != now.date():
                self.last_daily = now.date()
                run_jobs(self.daily)
            stats = self.deliver(self.batch_size)
        except Exception as e:
            self.failures += 1
            wait = self._backoff()
            logger.error(f"worker round failed ({self.failures} in a row), next try in {wait:.0f}s: {e}",
                         exc_info=True)
            return wait
        self.failures = 0
        if stats.get("claimed", 0):
            logger.info(f"worker delivery: {stats}")
        # a full batch means more rows are waiting right now
        return 0 if stats.get("claimed", 0) >= self.batch_size else self.poll_seconds

    def run(self) -> None:
        logger.info(
            f"worker started: batch {self.batch_size}, poll {self.poll_seconds}s, jobs every "
            f"{self.jobs_every_seconds:.0f}s, daily from {self.daily_hour}:00 UTC, channel {CHANNEL}"
        )
        while not self.stop.is_set():
            wait = self.tick()
            if wait:
                self.stop.wait(wait)
        logger.info("worker stopped")

    def install_signal_handlers(self) -> None:
        def _stop(signum, _frame):
            logger.info(f"worker got signal {signum}, finishing the current round")
            self.stop.set()

        signal.signal(signal.SIGTERM, _stop)
        signal.signal(signal.SIGINT, _stop)


def main() -> int:
    worker = Worker.from_env()
    worker.install_signal_handlers()
    worker.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
