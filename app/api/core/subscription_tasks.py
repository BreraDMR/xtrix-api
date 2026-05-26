from datetime import datetime
from sqlalchemy.orm import Session
from app.db.session import SessionLocal
from app.api.crud import crud_subscription
from app.api.core.logger import logger
from app.api.core.subscription_notifications import check_and_notify_expiring_subscriptions
from app.api.core.product_notifications import check_and_notify_low_stock
def deactivate_expired_subscriptions():
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
def process_subscription_notifications():
    db: Session = SessionLocal()
    try:
        count = check_and_notify_expiring_subscriptions(db)
        if count > 0:
            logger.info(f"Sent {count} subscription expiry notifications")
        return count
    except Exception as e:
        logger.error(f"Error sending subscription notifications: {e}")
        raise
    finally:
        db.close()
def run_all_subscription_tasks():
    try:
        deactivate_expired_subscriptions()
        process_subscription_notifications()
    except Exception as e:
        logger.error(f"Error running subscription tasks: {e}")
        raise
def process_product_notifications():
    db: Session = SessionLocal()
    try:
        count = check_and_notify_low_stock(db)
        if count > 0:
            logger.info(f"Sent {count} low stock notifications")
        return count
    except Exception as e:
        logger.error(f"Error sending product notifications: {e}")
        raise
    finally:
        db.close()
