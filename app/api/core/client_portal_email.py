"""Queue and render one-time sign-in links for the visitor portal.

The outbox stores the business and client ids, not the link. The raw token is created
only when the message is rendered, so the database never keeps a usable credential.
The link is built from FRONTEND_URL, never from a request header.
"""
import secrets
from html import escape
from urllib.parse import quote, urlsplit

from sqlalchemy.orm import Session

from app.api.core.config import settings
from app.api.core.logger import get_logger
from app.api.core.notification_outbox import enqueue_notification
from app.api.core.notification_transport import OutboundMessage
from app.api.crud import crud_client_portal
from app.db.models.business import Business
from app.db.models.client import Client
from app.db.models.client_business_relation import ClientBusinessRelation
from app.db.models.notification_outbox import NotificationOutbox

KIND = "client_portal_login"
FALLBACK = "en"
logger = get_logger("client_portal_email")

_TEXTS = {
    "en": (
        "Your Xtrix client area sign-in",
        (
            "Hello {name},\n\nuse this link to open your client area at {team}. "
            "The link is valid for 30 minutes and can be used once:\n\n{link}\n\n"
            "If you did not ask for it, ignore this email.\n\n{sign}"
        ),
        "The Xtrix team",
    ),
    "ru": (
        "Вход в клиентский кабинет Xtrix",
        (
            "Здравствуйте, {name}!\n\nПо этой ссылке можно открыть ваш клиентский кабинет в {team}. "
            "Ссылка действует 30 минут и может быть использована один раз:\n\n{link}\n\n"
            "Если вы её не запрашивали, просто проигнорируйте письмо.\n\n{sign}"
        ),
        "Команда Xtrix",
    ),
    "cz": (
        "Přihlášení do klientského portálu Xtrix",
        (
            "Dobrý den, {name},\n\ntento odkaz otevře váš klientský portál v {team}. "
            "Odkaz je platný 30 minut a lze jej použít pouze jednou:\n\n{link}\n\n"
            "Pokud jste o něj nežádali, tento e-mail ignorujte.\n\n{sign}"
        ),
        "Tým Xtrix",
    ),
}


class ClientPortalEmailError(Exception):
    pass


def _frontend_base() -> str:
    base = (settings.frontend_url or "").strip().rstrip("/")
    parts = urlsplit(base)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise ClientPortalEmailError("frontend_url is not configured")
    return base


def _html(body: str, link: str) -> str:
    anchor = f'<a href="{escape(link, quote=True)}">{escape(link)}</a>'
    paragraphs = []
    for paragraph in body.split("\n\n"):
        escaped = escape(paragraph)
        if link:
            escaped = escaped.replace(escape(link), anchor)
        paragraphs.append(f"<p>{escaped}</p>")
    return "<html><body>" + "".join(paragraphs) + "</body></html>"


def queue_portal_login(db: Session, business: Business, client: Client) -> NotificationOutbox:
    if not client.contact_email:
        raise ClientPortalEmailError("client has no email")
    event_key = f"client:{client.id}:portal_login:{secrets.token_hex(12)}"
    return enqueue_notification(
        db,
        event_key=event_key,
        kind=KIND,
        recipient=client.contact_email,
        business_id=business.id,
        language=client.language if client.language in _TEXTS else None,
        payload={"business_id": business.id, "client_id": client.id},
    )


def render_client_portal_login(db: Session, row: NotificationOutbox) -> OutboundMessage:
    payload = row.payload or {}
    business = db.get(Business, payload.get("business_id")) if payload.get("business_id") else None
    client = db.get(Client, payload.get("client_id")) if payload.get("client_id") else None
    if business is None or business.deleted_at is not None or client is None or client.deleted_at is not None:
        raise ClientPortalEmailError("portal client or business not found")
    relation = (
        db.query(ClientBusinessRelation.id)
        .filter(
            ClientBusinessRelation.client_id == client.id,
            ClientBusinessRelation.business_id == business.id,
        )
        .first()
    )
    if relation is None:
        raise ClientPortalEmailError("client is no longer attached to the business")
    if not client.contact_email or client.contact_email.lower() != row.recipient.lower():
        raise ClientPortalEmailError("the client email changed after the request")

    raw_token = crud_client_portal.issue_magic_token(db, business.id, client.id)
    link = f"{_frontend_base()}/client/portal#token={quote(raw_token, safe='')}"
    language = row.language if row.language in _TEXTS else client.language if client.language in _TEXTS else FALLBACK
    subject, body, sign = _TEXTS[language]
    values = {"name": client.full_name or "", "team": business.name, "link": link, "sign": sign}
    logger.info(f"Portal sign-in email prepared for client_id={client.id}, business_id={business.id}")
    return OutboundMessage(
        channel=row.channel,
        recipient=row.recipient,
        subject=subject.format(**values),
        text=body.format(**values),
        html=_html(body.format(**values), link),
    )
