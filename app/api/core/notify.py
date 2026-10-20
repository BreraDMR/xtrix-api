import logging
from typing import Iterable, Optional
from datetime import datetime
from app.api.core.client_messages import render
from app.api.core.notification_transport import OutboundMessage, get_notification_transport
logger = logging.getLogger(__name__)
# Everything here goes to a business's clients, so the text is rendered in the client's
# own language (Client.language), not the staff UI language.


def _send_email(to: str, subject: str, body: str) -> bool:
    result = get_notification_transport().send(
        OutboundMessage(channel="email", recipient=to, subject=subject, text=body)
    )
    return result.delivered


def _send_sms(phone: str, body: str) -> bool:
    result = get_notification_transport().send(
        OutboundMessage(channel="sms", recipient=phone, text=body)
    )
    return result.delivered


def send_whatsapp_notification(phone: str, message: str) -> bool:
    result = get_notification_transport().send(
        OutboundMessage(channel="whatsapp", recipient=phone, text=message)
    )
    return result.delivered


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
            logger.info("Notify (log only, not delivered): visit_id=%s client_id=%s at %s", v.id, v.client_id, v.date_start)
            ok = False
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
