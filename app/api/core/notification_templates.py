"""Localized email texts for booking lifecycle notifications.

The outbox stores template variables, not a rendered message: the link is issued when the
message is about to be sent and the raw token only lives in the email, never in the database.
HTML is built by escaping the whole text, so client names and service names cannot inject tags.
"""
from datetime import datetime, timezone
from html import escape
from typing import Optional
from urllib.parse import quote

from sqlalchemy.orm import Session

from app.api.core import booking_manage_access
from app.api.core.availability import salon_zone
from app.api.core.config import settings
from app.api.core.notification_transport import DeliveryResult, OutboundMessage, get_notification_transport
from app.db.models.notification_outbox import NotificationOutbox
from app.db.models.visit import Visit

FALLBACK = "ru"

_SIGN = {
    "en": "Best regards,\nthe {team} team",
    "ru": "С уважением,\nкоманда {team}",
    "cz": "S pozdravem,\ntým {team}",
}

_TEXTS = {
    "booking_created": {
        "en": (
            "Booking received",
            "Hello {name},\n\nyour booking is reserved for {when}.\nService: {service}\nBusiness: {team}\n\nManage your booking: {link}\n\n{sign}",
        ),
        "ru": (
            "Запись создана",
            "Здравствуйте, {name}!\n\nВаша запись создана на {when}.\nУслуга: {service}\nСалон: {team}\n\nУправление записью: {link}\n\n{sign}",
        ),
        "cz": (
            "Rezervace vytvořena",
            "Dobrý den, {name},\n\nvaše rezervace je vytvořena na {when}.\nSlužba: {service}\nSalon: {team}\n\nSpráva rezervace: {link}\n\n{sign}",
        ),
    },
    "booking_reminder": {
        "en": (
            "Appointment reminder",
            "Hello {name},\n\nthis is a reminder of your appointment on {when}.\nService: {service}\nBusiness: {team}\n\nManage your booking: {link}\n\n{sign}",
        ),
        "ru": (
            "Напоминание о визите",
            "Здравствуйте, {name}!\n\nНапоминаем о вашем визите {when}.\nУслуга: {service}\nСалон: {team}\n\nУправление записью: {link}\n\n{sign}",
        ),
        "cz": (
            "Připomínka návštěvy",
            "Dobrý den, {name},\n\npřipomínáme vaši návštěvu {when}.\nSlužba: {service}\nSalon: {team}\n\nSpráva rezervace: {link}\n\n{sign}",
        ),
    },
    "booking_changed": {
        "en": (
            "Your booking was moved",
            "Hello {name},\n\nyour booking has been moved to {when}.\nService: {service}\nBusiness: {team}\n\nManage your booking: {link}\n\n{sign}",
        ),
        "ru": (
            "Запись перенесена",
            "Здравствуйте, {name}!\n\nВаша запись перенесена на {when}.\nУслуга: {service}\nСалон: {team}\n\nУправление записью: {link}\n\n{sign}",
        ),
        "cz": (
            "Rezervace byla přeložena",
            "Dobrý den, {name},\n\nvaše rezervace byla přeložena na {when}.\nSlužba: {service}\nSalon: {team}\n\nSpráva rezervace: {link}\n\n{sign}",
        ),
    },
    "booking_cancelled": {
        "en": (
            "Your booking was cancelled",
            "Hello {name},\n\nyour booking on {when} has been cancelled.\nService: {service}\nBusiness: {team}\n\nThe booking page still shows the details: {link}\n\n{sign}",
        ),
        "ru": (
            "Запись отменена",
            "Здравствуйте, {name}!\n\nВаша запись на {when} отменена.\nУслуга: {service}\nСалон: {team}\n\nДетали записи остаются на странице: {link}\n\n{sign}",
        ),
        "cz": (
            "Rezervace byla zrušena",
            "Dobrý den, {name},\n\nvaše rezervace na {when} byla zrušena.\nSlužba: {service}\nSalon: {team}\n\nPodrobnosti rezervace zůstávají na stránce: {link}\n\n{sign}",
        ),
    },
}


class NotificationTemplateError(Exception):
    pass


def _when(value: Optional[str], timezone_name: Optional[str]) -> str:
    if not value:
        return ""
    try:
        moment = datetime.fromisoformat(value)
    except ValueError:
        return value
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    zone = salon_zone(timezone_name or "UTC")
    return moment.astimezone(zone).strftime("%d.%m.%Y %H:%M")


def _manage_link(db: Session, visit: Visit) -> str:
    raw, _token = booking_manage_access.issue_token(db, visit)
    return f"{settings.frontend_url.rstrip('/')}/manage#token={quote(raw, safe='')}"


def _html(body: str, link: str) -> str:
    anchor = f'<a href="{escape(link, quote=True)}">{escape(link)}</a>'
    paragraphs = []
    for paragraph in body.split("\n\n"):
        escaped = escape(paragraph)
        if link:
            escaped = escaped.replace(escape(link), anchor)
        paragraphs.append(f"<p>{escaped}</p>")
    return "<html><body>" + "".join(paragraphs) + "</body></html>"


def render_outbox_notification(db: Session, row: NotificationOutbox) -> OutboundMessage:
    if row.kind not in _TEXTS:
        raise NotificationTemplateError(f"unknown notification kind: {row.kind}")
    visit = db.get(Visit, row.visit_id) if row.visit_id else None
    if visit is None:
        raise NotificationTemplateError("notification has no live visit")
    language = row.language if row.language in _SIGN else FALLBACK
    payload = row.payload or {}
    team = payload.get("business_name") or "Xtrix"
    link = _manage_link(db, visit)
    values = {
        "name": payload.get("client_name") or "",
        "when": _when(payload.get("start"), payload.get("business_timezone")),
        "service": payload.get("service_name") or "",
        "team": team,
        "link": link,
        "sign": _SIGN[language].format(team=team),
    }
    subject, body = _TEXTS[row.kind][language]
    subject = subject.format(**values)
    body = body.format(**values)
    return OutboundMessage(
        channel=row.channel,
        recipient=row.recipient,
        subject=subject,
        text=body,
        html=_html(body, link),
    )


def send_outbox_message(db: Session, row: NotificationOutbox) -> DeliveryResult:
    message = render_outbox_notification(db, row)
    return get_notification_transport().send(message)
