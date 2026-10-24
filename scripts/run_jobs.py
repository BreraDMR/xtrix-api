"""Scheduled jobs: reminders, follow-ups, birthdays, subscriptions, low stock, monthly archive.

Nothing in the app runs these on its own, so either:
  python scripts/run_jobs.py --loop          # long-running worker (docker-compose "worker" service)
  python scripts/run_jobs.py reminders ...   # one-off, e.g. from cron on the host

Reminders are materialized in the notification outbox and delivered through the configured
transport; the old direct reminder path is no longer scheduled. Greetings and follow-ups keep
their own sent log. Every job is safe to run as often as you like: outbox event keys and the
sent log stop duplicates, and a failed delivery is retried on a later run.

Env: JOBS_CHANNEL (email|sms|whatsapp, default email) for greetings/follow-ups,
JOBS_EVERY_MINUTES (default 15), JOBS_DAILY_HOUR (UTC hour from which the daily ones may go
out, default 8). NOTIFICATION_TRANSPORT=capture keeps outbox messages in memory for local checks.
"""
import argparse
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.api.core import finance_tasks, import_service, notification_jobs, notification_tasks, subscription_tasks  # noqa: E402
from app.api.core.clock import utcnow  # noqa: E402
from app.api.core.logger import logger  # noqa: E402

CHANNEL = os.environ.get("JOBS_CHANNEL", "email")

FREQUENT = {
    "import_sources": import_service.purge_expired_sources,
    "reminders": notification_jobs.sync_and_deliver,
    "campaigns": notification_jobs.deliver_campaigns,
    "followups": lambda: notification_tasks.send_visit_followups(CHANNEL),
}
DAILY = {
    "birthdays": lambda: notification_tasks.send_birthday_greetings(CHANNEL),
    "subscriptions": subscription_tasks.run_all_subscription_tasks,
    "low_stock": subscription_tasks.process_product_notifications,
    "finance_archive": finance_tasks.check_and_run_monthly_archive,
}
JOBS = {**FREQUENT, **DAILY}


def run(names) -> bool:
    ok = True
    for name in names:
        try:
            result = JOBS[name]()
            logger.info(f"job {name}: {result}")
        except Exception as e:
            # one broken job must not stop the others
            ok = False
            logger.error(f"job {name} failed: {e}", exc_info=True)
    return ok


def loop() -> None:
    every = int(os.environ.get("JOBS_EVERY_MINUTES", "15")) * 60
    daily_hour = int(os.environ.get("JOBS_DAILY_HOUR", "8"))
    logger.info(f"jobs worker started: every {every // 60} min, daily jobs from {daily_hour}:00 UTC, greeting channel {CHANNEL}")
    while True:
        names = list(FREQUENT)
        # daily ones are idempotent too, the hour just keeps birthday wishes from arriving at 3 a.m.
        if utcnow().hour >= daily_hour:
            names += list(DAILY)
        run(names)
        time.sleep(every)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("jobs", nargs="*", choices=[*JOBS, "all"], help="jobs to run once")
    parser.add_argument("--loop", action="store_true", help="keep running as a worker")
    args = parser.parse_args()
    if args.loop:
        loop()
        return 0
    names = list(JOBS) if not args.jobs or "all" in args.jobs else args.jobs
    return 0 if run(names) else 1


if __name__ == "__main__":
    sys.exit(main())
