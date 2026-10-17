from datetime import datetime, timedelta
from typing import List
from sqlalchemy.orm import Session
from app.db.models.subscription import Subscription
from app.db.models.user import User
from app.api.core.logger import logger, mask_email, mask_phone
def send_subscription_expiry_notifications(db: Session, days_before: int = 3) -> int:
    notification_date = datetime.utcnow() + timedelta(days=days_before)
    notification_date_end = notification_date + timedelta(days=1)
    expiring_subscriptions = (
        db.query(Subscription)
        .join(User, Subscription.user_id == User.id)
        .filter(
            Subscription.status.in_(["active", "pending_cancellation"]),
            Subscription.current_period_end >= notification_date,
            Subscription.current_period_end < notification_date_end,
            Subscription.is_trial == False,  
        )
        .all()
    )
    sent_count = 0
    for sub in expiring_subscriptions:
        try:
            user = sub.user
            if user.email:
                send_expiry_email(user.email, user.full_name, sub.current_period_end, days_before)
                logger.info(f"Sent expiry email to {mask_email(user.email)} for subscription {sub.id}")
            if user.phone:
                try:
                    send_expiry_sms(user.phone, user.full_name, sub.current_period_end, days_before)
                    logger.info(f"Sent expiry SMS to {mask_phone(user.phone)} for subscription {sub.id}")
                except Exception as e:
                    logger.warning(f"Failed to send SMS to {mask_phone(user.phone)}: {e}")
            sent_count += 1
        except Exception as e:
            logger.error(f"Failed to send notification for subscription {sub.id}: {e}")
    return sent_count
def send_expiry_email(email: str, user_name: str, expiry_date: datetime, days_before: int) -> None:
    import smtplib
    from email.message import EmailMessage
    from app.api.core.config import settings
    if not settings.smtp_host or not settings.smtp_from:
        logger.warning(f"SMTP not configured, skip email to {mask_email(email)}")
        return
    subject = f"Ваша подписка истекает через {days_before} дн."
    if days_before == 1:
        subject = "Ваша подписка истекает завтра"
    elif days_before == 0:
        subject = "Ваша подписка истекает сегодня"
    expiry_str = expiry_date.strftime("%d.%m.%Y")
    body = f"""Здравствуйте, {user_name}!
Это автоматическое уведомление о том, что ваша подписка на Xtrix истекает {expiry_str}.
Для продолжения использования сервиса, пожалуйста, обновите подписку:
- Перейдите в раздел "Подписка" в вашем личном кабинете
- Выберите план и продолжите подписку
Если у вас возникли вопросы, пожалуйста, свяжитесь с нашей службой поддержки.
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
        logger.error(f"Failed to send email to {mask_email(email)}: {e}")
        raise
def send_expiry_sms(phone: str, user_name: str, expiry_date: datetime, days_before: int) -> None:
    import requests
    from app.api.core.config import settings
    if not settings.twilio_sid or not settings.twilio_token or not settings.twilio_from:
        logger.warning(f"Twilio not configured, skip SMS to {mask_phone(phone)}")
        return
    expiry_str = expiry_date.strftime("%d.%m.%Y")
    if days_before == 0:
        message = f"Xtrix: Ваша подписка истекает сегодня ({expiry_str}). Обновите подписку в личном кабинете."
    elif days_before == 1:
        message = f"Xtrix: Ваша подписка истекает завтра ({expiry_str}). Обновите подписку в личном кабинете."
    else:
        message = f"Xtrix: Ваша подписка истекает через {days_before} дн. ({expiry_str}). Обновите подписку в личном кабинете."
    try:
        url = f"https://api.twilio.com/2010-04-01/Accounts/{settings.twilio_sid}/Messages.json"
        data = {
            "From": settings.twilio_from,
            "To": phone,
            "Body": message,
        }
        resp = requests.post(url, data=data, auth=(settings.twilio_sid, settings.twilio_token), timeout=10)
        if resp.status_code < 200 or resp.status_code >= 300:
            logger.error(f"Twilio send failed: {resp.status_code} {resp.text}")
            raise Exception(f"Twilio API error: {resp.status_code}")
    except Exception as e:
        logger.error(f"Failed to send SMS to {mask_phone(phone)}: {e}")
        raise
def check_and_notify_expiring_subscriptions(db: Session) -> int:
    total_sent = 0
    total_sent += send_subscription_expiry_notifications(db, days_before=7)
    total_sent += send_subscription_expiry_notifications(db, days_before=3)
    total_sent += send_subscription_expiry_notifications(db, days_before=1)
    total_sent += send_subscription_expiry_notifications(db, days_before=0)
    return total_sent
