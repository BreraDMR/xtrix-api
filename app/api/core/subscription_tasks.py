"""
Фоновые задачи для управления подписками.
Вызываются периодически (например, через cron или celery).
"""
from datetime import datetime
from sqlalchemy.orm import Session
from app.db.session import SessionLocal
from app.api.crud import crud_subscription
from app.api.core.logger import logger


def deactivate_expired_subscriptions():
    """
    Деактивирует истекшие подписки.
    Должна вызываться периодически (например, каждый час или день).
    """
    db: Session = SessionLocal()
    try:
        count = crud_subscription.deactivate_expired_subscriptions(db)
        if count > 0:
            logger.info(f"Deactivated {count} expired subscriptions")
        return count
    except Exception as e:
        logger.error(f"Error deactivating expired subscriptions: {e}")
        raise
    finally:
        db.close()

