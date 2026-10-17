from typing import List, Optional
from sqlalchemy.orm import Session
from app.db.models.product import Product
from app.db.models.user import User
from app.api.core.logger import logger, mask_email
from app.api.crud import crud_product
def send_low_stock_notifications(db: Session, business_id: Optional[int] = None) -> int:
    low_stock_products = crud_product.get_low_stock_products(db, business_id=business_id)
    if not low_stock_products:
        return 0
    sent_count = 0
    products_by_business = {}
    for product in low_stock_products:
        if product.business_id not in products_by_business:
            products_by_business[product.business_id] = []
        products_by_business[product.business_id].append(product)
    for business_id, products in products_by_business.items():
        try:
            from app.db.models.business import Business
            business = db.query(Business).filter(Business.id == business_id).first()
            if not business:
                continue
            admins = db.query(User).filter(
                User.business_id == business_id,
                User.role.in_(["admin", "business_owner"]),
            ).all()
            if not admins:
                logger.warning(f"No admins found for business {business_id} to send low stock notifications")
                continue
            products_list = "\n".join([
                f"- {p.name}: {p.quantity} {p.unit} (минимум: {p.min_quantity} {p.unit})"
                for p in products
            ])
            for admin in admins:
                if admin.email:
                    send_low_stock_email(
                        admin.email,
                        admin.full_name,
                        business.name,
                        products_list,
                        len(products),
                    )
                    logger.info(f"Sent low stock email to {mask_email(admin.email)} for business {business_id}")
                    sent_count += 1
        except Exception as e:
            logger.error(f"Failed to send low stock notifications for business {business_id}: {e}")
    return sent_count
def send_low_stock_email(
    email: str,
    user_name: str,
    business_name: str,
    products_list: str,
    products_count: int,
) -> None:
    import smtplib
    from email.message import EmailMessage
    from app.api.core.config import settings
    if not settings.smtp_host or not settings.smtp_from:
        logger.warning(f"SMTP not configured, skip low stock email to {mask_email(email)}")
        return
    subject = f"⚠️ Низкий остаток товаров на складе ({products_count} шт.)"
    body = f"""Здравствуйте, {user_name}!
Это автоматическое уведомление о том, что на складе бизнеса "{business_name}" заканчиваются следующие товары:
{products_list}
Пожалуйста, пополните склад во избежание нехватки товаров.
Вы можете просмотреть детальную информацию в разделе "Склад" вашего личного кабинета.
С уважением,
Команда Xtrix
"""
    try:
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = settings.smtp_from
        msg["To"] = email
        msg.set_content(body)
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port) as server:
            server.starttls()
            if settings.smtp_user and settings.smtp_password:
                server.login(settings.smtp_user, settings.smtp_password)
            server.send_message(msg)
    except Exception as e:
        logger.error(f"Failed to send low stock email to {mask_email(email)}: {e}")
        raise
def check_and_notify_low_stock(db: Session, business_id: Optional[int] = None) -> int:
    return send_low_stock_notifications(db, business_id=business_id)
