"""Remembers which scheduled messages already went out.

Jobs ask was_sent() before sending and call mark_sent() only after a successful send,
so a job that runs every few minutes (or twice by mistake) never repeats a message,
and one that failed (SMTP down) tries again next time.
"""
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models.notification_log import NotificationLog


def was_sent(db: Session, kind: str, key: str) -> bool:
    return db.query(NotificationLog.id).filter(NotificationLog.kind == kind, NotificationLog.key == key).first() is not None


def mark_sent(db: Session, kind: str, key: str) -> None:
    db.add(NotificationLog(kind=kind, key=key))
    try:
        db.commit()
    except IntegrityError:
        # another run got there first - fine, it's sent either way
        db.rollback()
