"""Run reminder scheduling and outbox delivery from the existing job worker.

The legacy direct reminder job ignored per-business settings and sent immediately. This job
first materializes reminders for enabled businesses, then delivers due outbox rows through the
configured transport. With the capture transport messages stay in memory, so a local run can
never report a fake delivery.
"""
from sqlalchemy.orm import Session

from app.api.core import notification_scheduler, notification_templates, notification_transport
from app.api.core import notification_worker
from app.api.core.config import settings
from app.api.core.logger import logger
from app.db.session import SessionLocal


def sync_and_deliver() -> dict:
    db: Session = SessionLocal()
    try:
        transport = notification_transport.get_notification_transport()
        provider_ready = bool(settings.smtp_host and settings.smtp_from)
        if transport.name == "capture":
            logger.warning(
                "notification job: capture transport is active; messages are kept in memory and NOT delivered"
            )
        elif not provider_ready:
            logger.warning(
                "notification job: email provider is not configured; nothing will be sent"
            )
        synced = notification_scheduler.sync_reminders(db)
        delivered = notification_worker.run_once(
            db, lambda row: notification_templates.send_outbox_message(db, row)
        )
        result = {"reminders": synced, "delivery": delivered}
        logger.info(
            "notification job: transport %s, scheduled %s, cancelled %s, claimed %s, sent %s, "
            "captured %s, retried %s, failed %s, skipped %s, cancelled at delivery %s",
            transport.name,
            synced.get("scheduled", 0),
            synced.get("cancelled", 0),
            delivered.get("claimed", 0),
            delivered.get("sent", 0),
            delivered.get("captured", 0),
            delivered.get("retried", 0),
            delivered.get("failed", 0),
            delivered.get("skipped", 0),
            delivered.get("cancelled", 0),
        )
        return result
    finally:
        db.close()


def deliver_campaigns() -> dict:
    """Deliver due outbox rows, including campaigns the owner explicitly confirmed.

    Campaigns are never queued by this job - the send endpoint does that. This only drains
    what is already due, so a scheduled campaign leaves on the next frequent job run.
    """
    db: Session = SessionLocal()
    try:
        transport = notification_transport.get_notification_transport()
        if transport.name == "capture":
            logger.warning(
                "campaign job: capture transport is active; campaign messages are NOT delivered"
            )
        elif not (settings.smtp_host and settings.smtp_from):
            logger.warning(
                "campaign job: email provider is not configured; nothing will be sent"
            )
        delivered = notification_worker.run_once(
            db, lambda row: notification_templates.send_outbox_message(db, row)
        )
        logger.info(
            "campaign job: transport %s, claimed %s, sent %s, captured %s, retried %s, "
            "failed %s, skipped %s, cancelled at delivery %s",
            transport.name,
            delivered.get("claimed", 0),
            delivered.get("sent", 0),
            delivered.get("captured", 0),
            delivered.get("retried", 0),
            delivered.get("failed", 0),
            delivered.get("skipped", 0),
            delivered.get("cancelled", 0),
        )
        return {"delivery": delivered}
    finally:
        db.close()
