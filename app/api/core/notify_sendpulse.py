from typing import Optional
from datetime import datetime
from app.api.core.client_messages import render
from app.api.core.sendpulse import get_sendpulse_client
from app.api.core.logger import logger, mask_email, mask_phone
def send_email_via_sendpulse(
    email: str,
    subject: str,
    html_content: Optional[str] = None,
    text_content: Optional[str] = None,
) -> bool:
    try:
        client = get_sendpulse_client()
        return client.send_email(
            email=email,
            subject=subject,
            html=html_content,
            text=text_content,
        )
    except Exception as e:
        logger.error(f"Failed to send email via SendPulse to {mask_email(email)}: {e}")
        return False
def send_sms_via_sendpulse(phone: str, message: str) -> bool:
    try:
        client = get_sendpulse_client()
        return client.send_sms(phone=phone, message=message)
    except Exception as e:
        logger.error(f"Failed to send SMS via SendPulse to {mask_phone(phone)}: {e}")
        return False
# Client-facing texts come from client_messages, in the client's language (Client.language).


def _html(text: str) -> str:
    from html import escape
    return "<html><body>" + "".join(f"<p>{escape(p)}</p>" for p in text.split("\n\n")) + "</body></html>"


def _email(email: str, msg) -> bool:
    return send_email_via_sendpulse(email, msg.subject, _html(msg.body), msg.body)


def send_visit_reminder_email_sendpulse(email: str, visit, business_name: Optional[str] = None,
                                        language: Optional[str] = None, name: str = "") -> bool:
    return _email(email, render("reminder", language, name=name, when=visit.date_start,
                                service=getattr(visit, "service_name", "") or "", business_name=business_name))


def send_visit_reminder_sms_sendpulse(phone: str, visit, business_name: Optional[str] = None,
                                      language: Optional[str] = None, name: str = "") -> bool:
    msg = render("reminder", language, name=name, when=visit.date_start,
                 service=getattr(visit, "service_name", "") or "", business_name=business_name)
    return send_sms_via_sendpulse(phone, msg.short)


def send_birthday_greeting_email_sendpulse(email: str, client_name: str, business_name: Optional[str] = None,
                                           language: Optional[str] = None) -> bool:
    return _email(email, render("birthday", language, name=client_name, business_name=business_name))


def send_birthday_greeting_sms_sendpulse(phone: str, client_name: str, business_name: Optional[str] = None,
                                         language: Optional[str] = None) -> bool:
    return send_sms_via_sendpulse(phone, render("birthday", language, name=client_name, business_name=business_name).short)


def send_followup_email_sendpulse(email: str, client_name: str, visit_date: datetime, service_name: str,
                                  business_name: Optional[str] = None, language: Optional[str] = None) -> bool:
    return _email(email, render("followup", language, name=client_name, when=visit_date, service=service_name,
                                business_name=business_name))


def send_followup_sms_sendpulse(phone: str, client_name: str, visit_date: datetime, service_name: str,
                                business_name: Optional[str] = None, language: Optional[str] = None) -> bool:
    msg = render("followup", language, name=client_name, when=visit_date, service=service_name, business_name=business_name)
    return send_sms_via_sendpulse(phone, msg.short)


def send_verification_code_sendpulse(phone: str, code: str, language: Optional[str] = None) -> bool:
    return send_sms_via_sendpulse(phone, render("verification", language, text=code).short)


def send_loyalty_notification_email_sendpulse(email: str, client_name: str, message: str,
                                              business_name: Optional[str] = None, language: Optional[str] = None) -> bool:
    return _email(email, render("loyalty", language, name=client_name, text=message, business_name=business_name))


def send_loyalty_notification_sms_sendpulse(phone: str, client_name: str, message: str,
                                            business_name: Optional[str] = None, language: Optional[str] = None) -> bool:
    return send_sms_via_sendpulse(phone, render("loyalty", language, name=client_name, text=message,
                                                business_name=business_name).short)
