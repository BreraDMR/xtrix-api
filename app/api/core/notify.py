import logging
import smtplib
from email.message import EmailMessage
from typing import Iterable, Optional
from datetime import datetime

import requests

from app.api.core.config import settings

logger = logging.getLogger(__name__)


def send_notifications(visits: Iterable, channel: str = "log", contacts: Optional[dict] = None) -> int:
    """
    Отправка уведомлений клиентам.
    channel: log|email|sms|whatsapp
    contacts: dict[client_id] -> {"email": str, "phone": str}
    """
    count = 0
    contacts = contacts or {}
    for v in visits:
        info = contacts.get(v.client_id, {})
        if channel == "email":
            if info.get("email"):
                ok = send_email_notification(info["email"], v)
                if ok:
                    count += 1
        elif channel == "sms":
            if info.get("phone"):
                ok = send_sms_notification(info["phone"], v)
                if ok:
                    count += 1
        elif channel == "whatsapp":
            if info.get("phone"):
                message = f"Reminder: visit on {v.date_start} (service {getattr(v, 'service_name', '') or getattr(v, 'service_id', '')})"
                ok = send_whatsapp_notification(info["phone"], message)
                if ok:
                    count += 1
        else:
            logger.info("Notify (log): visit_id=%s client_id=%s at %s", v.id, v.client_id, v.date_start)
            count += 1
    return count


def send_email_notification(email: str, visit) -> bool:
    if not settings.smtp_host or not settings.smtp_from:
        logger.warning("SMTP not configured, skip email to %s", email)
        return False
    msg = EmailMessage()
    msg["Subject"] = "Appointment reminder"
    msg["From"] = settings.smtp_from
    msg["To"] = email
    msg.set_content(
        f"Reminder: visit on {visit.date_start} with specialist {visit.specialist_id}. "
        f"Service: {getattr(visit, 'service_name', '') or getattr(visit, 'service_id', '')}"
    )
    try:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port) as server:
            server.starttls()
            if settings.smtp_user and settings.smtp_password:
                server.login(settings.smtp_user, settings.smtp_password)
            server.send_message(msg)
        return True
    except Exception as e:
        logger.error("Email send failed: %s", e)
        return False


def send_sms_notification(phone: str, visit) -> bool:
    if not settings.twilio_sid or not settings.twilio_token or not settings.twilio_from:
        logger.warning("Twilio not configured, skip sms to %s", phone)
        return False
    url = f"https://api.twilio.com/2010-04-01/Accounts/{settings.twilio_sid}/Messages.json"
    data = {
        "From": settings.twilio_from,
        "To": phone,
        "Body": f"Reminder: visit on {visit.date_start} (service {getattr(visit, 'service_name', '') or getattr(visit, 'service_id', '')})",
    }
    try:
        resp = requests.post(url, data=data, auth=(settings.twilio_sid, settings.twilio_token), timeout=10)
        if resp.status_code >= 200 and resp.status_code < 300:
            return True
        logger.error("Twilio send failed: %s %s", resp.status_code, resp.text)
        return False
    except Exception as e:
        logger.error("Twilio send exception: %s", e)
        return False


def send_whatsapp_notification(phone: str, message: str) -> bool:
    """
    Отправка WhatsApp уведомления через Twilio.
    
    Args:
        phone: Номер телефона получателя (формат: +1234567890)
        message: Текст сообщения
    
    Returns:
        True если отправлено успешно, False в противном случае
    """
    if not settings.twilio_sid or not settings.twilio_token or not settings.twilio_whatsapp_from:
        logger.warning("Twilio WhatsApp not configured, skip WhatsApp to %s", phone)
        return False
    
    # Форматируем номер телефона для WhatsApp (добавляем whatsapp: если нужно)
    if not phone.startswith("whatsapp:"):
        if not phone.startswith("+"):
            phone = f"+{phone}"
        phone = f"whatsapp:{phone}"
    
    url = f"https://api.twilio.com/2010-04-01/Accounts/{settings.twilio_sid}/Messages.json"
    data = {
        "From": settings.twilio_whatsapp_from,
        "To": phone,
        "Body": message,
    }
    try:
        resp = requests.post(url, data=data, auth=(settings.twilio_sid, settings.twilio_token), timeout=10)
        if resp.status_code >= 200 and resp.status_code < 300:
            return True
        logger.error("Twilio WhatsApp send failed: %s %s", resp.status_code, resp.text)
        return False
    except Exception as e:
        logger.error("Twilio WhatsApp send exception: %s", e)
        return False


def send_birthday_greeting_email(email: str, client_name: str, business_name: Optional[str] = None) -> bool:
    """Отправка поздравления с днём рождения по email."""
    if not settings.smtp_host or not settings.smtp_from:
        logger.warning("SMTP not configured, skip birthday email to %s", email)
        return False
    
    msg = EmailMessage()
    msg["Subject"] = "🎉 С Днём Рождения!"
    msg["From"] = settings.smtp_from
    msg["To"] = email
    
    business_text = f" от {business_name}" if business_name else ""
    content = f"""Дорогая(ой) {client_name}!

Поздравляем Вас с Днём Рождения! 🎂🎉🎁

Желаем Вам крепкого здоровья, счастья, благополучия и всего самого наилучшего!

С уважением,
команда{business_text}"""
    
    msg.set_content(content)
    try:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port) as server:
            server.starttls()
            if settings.smtp_user and settings.smtp_password:
                server.login(settings.smtp_user, settings.smtp_password)
            server.send_message(msg)
        return True
    except Exception as e:
        logger.error("Birthday email send failed: %s", e)
        return False


def send_birthday_greeting_sms(phone: str, client_name: str, business_name: Optional[str] = None) -> bool:
    """Отправка поздравления с днём рождения по SMS."""
    if not settings.twilio_sid or not settings.twilio_token or not settings.twilio_from:
        logger.warning("Twilio not configured, skip birthday SMS to %s", phone)
        return False
    
    business_text = f" от {business_name}" if business_name else ""
    message = f"🎉 Дорогая(ой) {client_name}! Поздравляем Вас с Днём Рождения! Желаем здоровья, счастья и благополучия! С уважением, команда{business_text}"
    
    url = f"https://api.twilio.com/2010-04-01/Accounts/{settings.twilio_sid}/Messages.json"
    data = {
        "From": settings.twilio_from,
        "To": phone,
        "Body": message,
    }
    try:
        resp = requests.post(url, data=data, auth=(settings.twilio_sid, settings.twilio_token), timeout=10)
        if resp.status_code >= 200 and resp.status_code < 300:
            return True
        logger.error("Twilio birthday SMS send failed: %s %s", resp.status_code, resp.text)
        return False
    except Exception as e:
        logger.error("Twilio birthday SMS send exception: %s", e)
        return False


def send_birthday_greeting_whatsapp(phone: str, client_name: str, business_name: Optional[str] = None) -> bool:
    """Отправка поздравления с днём рождения по WhatsApp."""
    business_text = f" от {business_name}" if business_name else ""
    message = f"🎉 Дорогая(ой) {client_name}!\n\nПоздравляем Вас с Днём Рождения! 🎂🎁\n\nЖелаем Вам крепкого здоровья, счастья, благополучия и всего самого наилучшего!\n\nС уважением,\nкоманда{business_text}"
    return send_whatsapp_notification(phone, message)


def send_visit_followup_email(email: str, client_name: str, visit_date: datetime, service_name: str, business_name: Optional[str] = None) -> bool:
    """Отправка Follow-up сообщения после визита по email."""
    if not settings.smtp_host or not settings.smtp_from:
        logger.warning("SMTP not configured, skip followup email to %s", email)
        return False
    
    msg = EmailMessage()
    msg["Subject"] = "Спасибо за визит!"
    msg["From"] = settings.smtp_from
    msg["To"] = email
    
    business_text = f" от {business_name}" if business_name else ""
    visit_date_str = visit_date.strftime("%d.%m.%Y %H:%M") if isinstance(visit_date, datetime) else str(visit_date)
    
    content = f"""Дорогая(ой) {client_name}!

Спасибо, что посетили нас {visit_date_str}!

Мы надеемся, что услуга "{service_name}" была для Вас полезной и приятной.

Будем рады видеть Вас снова!

С уважением,
команда{business_text}"""
    
    msg.set_content(content)
    try:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port) as server:
            server.starttls()
            if settings.smtp_user and settings.smtp_password:
                server.login(settings.smtp_user, settings.smtp_password)
            server.send_message(msg)
        return True
    except Exception as e:
        logger.error("Followup email send failed: %s", e)
        return False


def send_visit_followup_sms(phone: str, client_name: str, visit_date: datetime, service_name: str, business_name: Optional[str] = None) -> bool:
    """Отправка Follow-up сообщения после визита по SMS."""
    if not settings.twilio_sid or not settings.twilio_token or not settings.twilio_from:
        logger.warning("Twilio not configured, skip followup SMS to %s", phone)
        return False
    
    business_text = f" от {business_name}" if business_name else ""
    visit_date_str = visit_date.strftime("%d.%m.%Y %H:%M") if isinstance(visit_date, datetime) else str(visit_date)
    message = f"Дорогая(ой) {client_name}! Спасибо за визит {visit_date_str}. Надеемся, услуга '{service_name}' была полезна. Будем рады видеть Вас снова! Команда{business_text}"
    
    url = f"https://api.twilio.com/2010-04-01/Accounts/{settings.twilio_sid}/Messages.json"
    data = {
        "From": settings.twilio_from,
        "To": phone,
        "Body": message,
    }
    try:
        resp = requests.post(url, data=data, auth=(settings.twilio_sid, settings.twilio_token), timeout=10)
        if resp.status_code >= 200 and resp.status_code < 300:
            return True
        logger.error("Twilio followup SMS send failed: %s %s", resp.status_code, resp.text)
        return False
    except Exception as e:
        logger.error("Twilio followup SMS send exception: %s", e)
        return False


def send_visit_followup_whatsapp(phone: str, client_name: str, visit_date: datetime, service_name: str, business_name: Optional[str] = None) -> bool:
    """Отправка Follow-up сообщения после визита по WhatsApp."""
    business_text = f" от {business_name}" if business_name else ""
    visit_date_str = visit_date.strftime("%d.%m.%Y %H:%M") if isinstance(visit_date, datetime) else str(visit_date)
    message = f"Дорогая(ой) {client_name}!\n\nСпасибо, что посетили нас {visit_date_str}!\n\nМы надеемся, что услуга '{service_name}' была для Вас полезной и приятной.\n\nБудем рады видеть Вас снова!\n\nС уважением,\nкоманда{business_text}"
    return send_whatsapp_notification(phone, message)
