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
    """Send password reset code via email using Mailgun API or SMTP."""
    
    # Try Mailgun API first (preferred)
    if settings.mailgun_api_key and settings.mailgun_domain:
        return _send_email_via_mailgun(email, code)
    
    # Fallback to SMTP
    if settings.smtp_host and settings.smtp_from:
        return _send_email_via_smtp(email, code)
    
    # Not configured
    logger.warning("Email not configured (no Mailgun or SMTP), skip password reset email to %s", email)
    logger.info("Password reset code for %s: %s", email, code)
    return False


def _send_email_via_mailgun(email: str, code: str) -> bool:
    """Send email using Mailgun API."""
    try:
        # HTML content
        html_content = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="utf-8">
            <style>
                body {{ font-family: Arial, sans-serif; line-height: 1.6; color: #333; }}
                .container {{ max-width: 600px; margin: 0 auto; padding: 20px; }}
                .code {{ font-size: 32px; font-weight: bold; text-align: center; 
                         background: #f0f0f0; padding: 20px; border-radius: 8px; 
                         letter-spacing: 8px; margin: 20px 0; color: #2563eb; }}
                .footer {{ margin-top: 30px; padding-top: 20px; border-top: 1px solid #eee; 
                          color: #666; font-size: 12px; }}
            </style>
        </head>
        <body>
            <div class="container">
                <h2>Password Reset Request</h2>
                <p>You requested a password reset for your Xtrix account.</p>
                <p>Your password reset code is:</p>
                <div class="code">{code}</div>
                <p>This code will expire in <strong>1 hour</strong>.</p>
                <p>If you didn't request a password reset, please ignore this email.</p>
                <div class="footer">
                    <p>Xtrix Team</p>
                </div>
            </div>
        </body>
        </html>
        """
        
        # Plain text version
        text_content = (
            f"Your password reset code is: {code}\n\n"
            f"This code will expire in 1 hour.\n\n"
            f"If you didn't request a password reset, please ignore this email.\n\n"
            f"Xtrix Team"
        )
        
        # Mailgun API endpoint
        url = f"{settings.mailgun_base_url}/v3/{settings.mailgun_domain}/messages"
        
        # Determine "from" address
        from_email = settings.smtp_from or f"noreply@{settings.mailgun_domain}"
        
        data = {
            "from": f"Xtrix <{from_email}>",
            "to": email,
            "subject": "Xtrix - Password Reset Code",
            "text": text_content,
            "html": html_content,
        }
        
        response = requests.post(
            url,
            auth=("api", settings.mailgun_api_key),
            data=data,
            timeout=10
        )
        
        if response.status_code == 200:
            logger.info("Password reset email sent successfully via Mailgun to %s", email)
            return True
        else:
            logger.error("Mailgun email send failed: %s %s", response.status_code, response.text)
            logger.info("Password reset code for %s: %s (logged due to Mailgun failure)", email, code)
            return False
            
    except Exception as e:
        logger.error("Mailgun email send exception: %s", str(e))
        logger.info("Password reset code for %s: %s (logged due to exception)", email, code)
        return False


def _send_email_via_smtp(email: str, code: str) -> bool:
    """Send email using SMTP."""
    msg = EmailMessage()
    msg["Subject"] = "Xtrix - Password Reset Code"
    msg["From"] = settings.smtp_from
    msg["To"] = email
    
    # Plain text version
    text_content = (
        f"Your password reset code is: {code}\n\n"
        f"This code will expire in 1 hour.\n\n"
        f"If you didn't request a password reset, please ignore this email.\n\n"
        f"Xtrix Team"
    )
    
    # HTML version
    html_content = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="utf-8">
        <style>
            body {{ font-family: Arial, sans-serif; line-height: 1.6; color: #333; }}
            .container {{ max-width: 600px; margin: 0 auto; padding: 20px; }}
            .code {{ font-size: 32px; font-weight: bold; text-align: center; 
                     background: #f0f0f0; padding: 20px; border-radius: 8px; 
                     letter-spacing: 8px; margin: 20px 0; color: #2563eb; }}
            .footer {{ margin-top: 30px; padding-top: 20px; border-top: 1px solid #eee; 
                      color: #666; font-size: 12px; }}
        </style>
    </head>
    <body>
        <div class="container">
            <h2>Password Reset Request</h2>
            <p>You requested a password reset for your Xtrix account.</p>
            <p>Your password reset code is:</p>
            <div class="code">{code}</div>
            <p>This code will expire in <strong>1 hour</strong>.</p>
            <p>If you didn't request a password reset, please ignore this email.</p>
            <div class="footer">
                <p>Xtrix Team</p>
            </div>
        </div>
    </body>
    </html>
    """
    
    msg.set_content(text_content)
    msg.add_alternative(html_content, subtype='html')
    
    try:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port) as server:
            server.starttls()
            if settings.smtp_user and settings.smtp_password:
                server.login(settings.smtp_user, settings.smtp_password)
            server.send_message(msg)
        logger.info("Password reset email sent successfully via SMTP to %s", email)
        return True
    except Exception as e:
        logger.error("SMTP email send failed: %s", str(e))
        logger.info("Password reset code for %s: %s (logged due to SMTP failure)", email, code)
        return False


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

