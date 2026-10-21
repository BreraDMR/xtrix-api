"""Time-limited waitlist offers delivered through the notification outbox.

The email link is just a page address with a fragment; the raw token is minted when the
message is rendered, so the database keeps only its digest. Rendering refuses an offer that
was declined, accepted or expired in the meantime: the recipient must never get a link to
a slot this offer no longer represents.
"""
from datetime import datetime, timezone
from html import escape
from urllib.parse import quote, urlsplit

from sqlalchemy.orm import Session

from app.api.core.availability import salon_zone
from app.api.core.clock import utcnow
from app.api.core.config import settings
from app.api.core.logger import get_logger
from app.api.core.notification_transport import OutboundMessage
from app.api.crud import crud_waitlist
from app.db.models.business import Business
from app.db.models.notification_outbox import NotificationOutbox
from app.db.models.service import Service
from app.db.models.user import User
from app.db.models.waitlist import WaitlistEntry, WaitlistOffer

KIND = "waitlist_offer"
FALLBACK = "en"
logger = get_logger("waitlist_email")

_TEXTS = {
    "en": (
        "A time opened up at {team}",
        (
            "Hello {name},\n\n"
            "a time that matches your waitlist request is free: {when}.\n"
            "Service: {service}\nMaster: {staff}\nBusiness: {team}\n\n"
            "This is an offer, not a confirmed booking. The time stays free for anyone "
            "until you confirm it, and the link works until {expires}.\n\n"
            "Confirm or decline: {link}\n\n{sign}"
        ),
        "The Xtrix team",
    ),
    "ru": (
        "Появилось время в {team}",
        (
            "Здравствуйте, {name}!\n\n"
            "Освободилось время, подходящее под вашу заявку в лист ожидания: {when}.\n"
            "Услуга: {service}\nМастер: {staff}\nСалон: {team}\n\n"
            "Это предложение, а не подтверждённая запись. Время остаётся свободным для всех, "
            "пока вы его не подтвердите; ссылка действует до {expires}.\n\n"
            "Подтвердить или отказаться: {link}\n\n{sign}"
        ),
        "Команда Xtrix",
    ),
    "cz": (
        "Uvolnil se čas v {team}",
        (
            "Dobrý den, {name},\n\n"
            "uvolnil se čas odpovídající vaší žádosti v pořadníku: {when}.\n"
            "Služba: {service}\nMistr: {staff}\nSalon: {team}\n\n"
            "Toto je nabídka, ne potvrzená rezervace. Čas zůstává volný pro všechny, dokud "
            "jej nepotvrdíte; odkaz platí do {expires}.\n\n"
            "Potvrdit nebo odmítnout: {link}\n\n{sign}"
        ),
        "Tým Xtrix",
    ),
}


class WaitlistEmailError(Exception):
    pass


def _frontend_base() -> str:
    base = (settings.frontend_url or "").strip().rstrip("/")
    parts = urlsplit(base)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise WaitlistEmailError("frontend_url is not configured")
    return base


def _when(value: datetime, timezone_name: str) -> str:
    moment = value.replace(tzinfo=timezone.utc)
    return moment.astimezone(salon_zone(timezone_name)).strftime("%d.%m.%Y %H:%M")


def _html(body: str, link: str) -> str:
    anchor = f'<a href="{escape(link, quote=True)}">{escape(link)}</a>'
    paragraphs = []
    for paragraph in body.split("\n\n"):
        escaped = escape(paragraph)
        if link:
            escaped = escaped.replace(escape(link), anchor)
        paragraphs.append(f"<p>{escaped}</p>")
    return "<html><body>" + "".join(paragraphs) + "</body></html>"


def render_waitlist_offer(db: Session, row: NotificationOutbox) -> OutboundMessage:
    payload = row.payload or {}
    offer = db.get(WaitlistOffer, payload.get("offer_id")) if payload.get("offer_id") else None
    if offer is None:
        raise WaitlistEmailError("waitlist offer not found")
    entry = db.get(WaitlistEntry, offer.entry_id)
    business = db.get(Business, offer.business_id)
    service = db.get(Service, offer.service_id)
    staff = db.get(User, offer.specialist_id)
    now = utcnow()
    if (
        entry is None
        or business is None
        or business.deleted_at is not None
        or service is None
        or staff is None
        or entry.status != "active"
        or offer.status != "pending"
        or offer.expires_at <= now
    ):
        raise WaitlistEmailError("the waitlist offer is no longer open")

    raw_token = crud_waitlist.issue_offer_token(offer)
    offer.updated_at = now
    db.commit()
    link = f"{_frontend_base()}/waitlist/offer#token={quote(raw_token, safe='')}"
    language = row.language if row.language in _TEXTS else entry.language if entry.language in _TEXTS else FALLBACK
    subject, body, sign = _TEXTS[language]
    values = {
        "name": entry.full_name or "",
        "when": _when(offer.start, business.timezone),
        "expires": _when(offer.expires_at, business.timezone),
        "service": service.name,
        "staff": staff.full_name,
        "team": business.name,
        "link": link,
        "sign": sign,
    }
    logger.info("Waitlist offer email prepared for offer_id=%s", offer.id)
    text = body.format(**values)
    return OutboundMessage(
        channel=row.channel,
        recipient=row.recipient,
        subject=subject.format(**values),
        text=text,
        html=_html(text, link),
    )
