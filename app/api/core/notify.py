import logging
import smtplib
from email.message import EmailMessage
from typing import Iterable, Optional

import requests

from app.api.core.config import settings

logger = logging.getLogger(__name__)


def send_notifications(visits: Iterable, channel: str = "log", contacts: Optional[dict] = None) -> int:
    """
    Отправка уведомлений клиентам.
    channel: log|email|sms
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


def send_password_reset_email(email: str, code: str) -> bool:
    """Send password reset code via email."""
    if not settings.smtp_host or not settings.smtp_from:
        logger.warning("SMTP not configured, skip password reset email to %s", email)
        # For development, log the code instead
        logger.info("Password reset code for %s: %s", email, code)
        return True  # Return True for development
    msg = EmailMessage()
    msg["Subject"] = "Password Reset Code"
    msg["From"] = settings.smtp_from
    msg["To"] = email
    msg.set_content(
        f"Your password reset code is: {code}\n\n"
        f"This code will expire in 1 hour.\n\n"
        f"If you didn't request a password reset, please ignore this email."
    )
    try:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port) as server:
            server.starttls()
            if settings.smtp_user and settings.smtp_password:
                server.login(settings.smtp_user, settings.smtp_password)
            server.send_message(msg)
        logger.info("Password reset email sent to %s", email)
        return True
    except Exception as e:
        logger.error("Password reset email send failed: %s", e)
        # For development, log the code
        logger.info("Password reset code for %s: %s", email, code)
        return True  # Return True for development


def send_password_reset_sms(phone: str, code: str) -> bool:
    """Send password reset code via SMS."""
    if not settings.twilio_sid or not settings.twilio_token or not settings.twilio_from:
        logger.warning("Twilio not configured, skip password reset SMS to %s", phone)
        # For development, log the code instead
        logger.info("Password reset code for %s: %s", phone, code)
        return True  # Return True for development
    url = f"https://api.twilio.com/2010-04-01/Accounts/{settings.twilio_sid}/Messages.json"
    data = {
        "From": settings.twilio_from,
        "To": phone,
        "Body": f"Your password reset code is: {code}. This code will expire in 1 hour.",
    }
    try:
        resp = requests.post(url, data=data, auth=(settings.twilio_sid, settings.twilio_token), timeout=10)
        if resp.status_code >= 200 and resp.status_code < 300:
            logger.info("Password reset SMS sent to %s", phone)
            return True
        logger.error("Twilio password reset SMS failed: %s %s", resp.status_code, resp.text)
        # For development, log the code
        logger.info("Password reset code for %s: %s", phone, code)
        return True  # Return True for development
    except Exception as e:
        logger.error("Twilio password reset SMS exception: %s", e)
        # For development, log the code
        logger.info("Password reset code for %s: %s", phone, code)
        return True  # Return True for development

