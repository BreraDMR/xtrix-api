from collections import defaultdict
from typing import Optional
from sqlalchemy.orm import Session
from app.db.models.business import Business
from app.db.models.user import User
from app.api.core import notify
from app.api.core.logger import logger, mask_email
from app.api.core.staff_messages import contact_for, low_stock
from app.api.crud import crud_product


def send_low_stock_notifications(db: Session, business_id: Optional[int] = None) -> int:
    """E-mail each business's owners/admins the list of products below their minimum."""
    by_business = defaultdict(list)
    for product in crud_product.get_low_stock_products(db, business_id=business_id):
        by_business[product.business_id].append(product)
    sent_count = 0
    for biz_id, products in by_business.items():
        try:
            business = db.query(Business).filter(Business.id == biz_id).first()
            if not business:
                continue
            admins = db.query(User).filter(
                User.business_id == biz_id,
                User.role.in_(["admin", "business_owner"]),
                User.deleted_at.is_(None),
            ).all()
            if not admins:
                logger.warning(f"No admins found for business {biz_id} to send low stock notifications")
                continue
            for admin in admins:
                contact = contact_for(db, admin)
                if not contact.email:
                    continue
                msg = low_stock(contact.language, admin.full_name, business.name, products)
                if notify._send_email(contact.email, msg.subject, msg.body):
                    sent_count += 1
                    logger.info(f"Sent low stock email to {mask_email(contact.email)} for business {biz_id}")
        except Exception as e:
            logger.error(f"Failed to send low stock notifications for business {biz_id}: {e}")
    return sent_count


def check_and_notify_low_stock(db: Session, business_id: Optional[int] = None) -> int:
    return send_low_stock_notifications(db, business_id=business_id)
