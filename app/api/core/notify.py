import logging
import smtplib
from email.message import EmailMessage
from typing import Iterable, Optional
from datetime import datetime
import requests
from app.api.core.client_messages import render
from app.api.core.config import settings
from app.api.core.logger import mask_email, mask_phone
logger = logging.getLogger(__name__)
# Everything here goes to a business's clients, so the text is rendered in the client's
# own language (Client.language), not the staff UI language.


def _send_email(to: str, subject: str, body: str) -> bool:
    if not settings.smtp_host or not settings.smtp_from:
        logger.warning("SMTP not configured, skip email to %s", mask_email(to))
        return False
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = settings.smtp_from
    msg["To"] = to
    msg.set_content(body)
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


def _send_twilio(sender: str, to: str, body: str) -> bool:
    url = f"https://api.twilio.com/2010-04-01/Accounts/{settings.twilio_sid}/Messages.json"
    try:
        resp = requests.post(url, data={"From": sender, "To": to, "Body": body},
                             auth=(settings.twilio_sid, settings.twilio_token), timeout=10)
        if 200 <= resp.status_code < 300:
            return True
        logger.error("Twilio send failed: %s %s", resp.status_code, resp.text)
        return False
    except Exception as e:
        logger.error("Twilio send exception: %s", e)
        return False


def _send_sms(phone: str, body: str) -> bool:
    if not settings.twilio_sid or not settings.twilio_token or not settings.twilio_from:
        logger.warning("Twilio not configured, skip sms to %s", mask_phone(phone))
        return False
    return _send_twilio(settings.twilio_from, phone, body)


def send_whatsapp_notification(phone: str, message: str) -> bool:
    if not settings.twilio_sid or not settings.twilio_token or not settings.twilio_whatsapp_from:
        logger.warning("Twilio WhatsApp not configured, skip WhatsApp to %s", mask_phone(phone))
        return False
    if not phone.startswith("whatsapp:"):
        if not phone.startswith("+"):
            phone = f"+{phone}"
        phone = f"whatsapp:{phone}"
    return _send_twilio(settings.twilio_whatsapp_from, phone, message)


def _reminder(visit, language: Optional[str], name: str = ""):
    service = getattr(visit, "service_name", "") or ""
    return render("reminder", language, name=name, when=visit.date_start, service=service)


def send_notifications(visits: Iterable, channel: str = "log", contacts: Optional[dict] = None) -> int:
    """contacts: {client_id: {"email", "phone", "language", "name"}}"""
    count = 0
    contacts = contacts or {}
    for v in visits:
        info = contacts.get(v.client_id, {})
        if channel == "email":
            ok = bool(info.get("email")) and send_email_notification(info["email"], v, info.get("language"), info.get("name", ""))
        elif channel == "sms":
            ok = bool(info.get("phone")) and send_sms_notification(info["phone"], v, info.get("language"), info.get("name", ""))
        elif channel == "whatsapp":
            ok = bool(info.get("phone")) and send_whatsapp_notification(
                info["phone"], _reminder(v, info.get("language"), info.get("name", "")).short)
        else:
            logger.info("Notify (log): visit_id=%s client_id=%s at %s", v.id, v.client_id, v.date_start)
            ok = True
        if ok:
            count += 1
    return count


def send_email_notification(email: str, visit, language: Optional[str] = None, name: str = "") -> bool:
    msg = _reminder(visit, language, name)
    return _send_email(email, msg.subject, msg.body)


def send_sms_notification(phone: str, visit, language: Optional[str] = None, name: str = "") -> bool:
    return _send_sms(phone, _reminder(visit, language, name).short)


def send_birthday_greeting_email(email: str, client_name: str, business_name: Optional[str] = None,
                                 language: Optional[str] = None) -> bool:
    msg = render("birthday", language, name=client_name, business_name=business_name)
    return _send_email(email, msg.subject, msg.body)


def send_birthday_greeting_sms(phone: str, client_name: str, business_name: Optional[str] = None,
                               language: Optional[str] = None) -> bool:
    return _send_sms(phone, render("birthday", language, name=client_name, business_name=business_name).short)


def send_birthday_greeting_whatsapp(phone: str, client_name: str, business_name: Optional[str] = None,
                                    language: Optional[str] = None) -> bool:
    msg = render("birthday", language, name=client_name, business_name=business_name)
    return send_whatsapp_notification(phone, msg.body)


def send_visit_followup_email(email: str, client_name: str, visit_date: datetime, service_name: str,
                              business_name: Optional[str] = None, language: Optional[str] = None) -> bool:
    msg = render("followup", language, name=client_name, when=visit_date, service=service_name, business_name=business_name)
    return _send_email(email, msg.subject, msg.body)


def send_visit_followup_sms(phone: str, client_name: str, visit_date: datetime, service_name: str,
                            business_name: Optional[str] = None, language: Optional[str] = None) -> bool:
    msg = render("followup", language, name=client_name, when=visit_date, service=service_name, business_name=business_name)
    return _send_sms(phone, msg.short)


def send_visit_followup_whatsapp(phone: str, client_name: str, visit_date: datetime, service_name: str,
                                 business_name: Optional[str] = None, language: Optional[str] = None) -> bool:
    msg = render("followup", language, name=client_name, when=visit_date, service=service_name, business_name=business_name)
    return send_whatsapp_notification(phone, msg.body)
