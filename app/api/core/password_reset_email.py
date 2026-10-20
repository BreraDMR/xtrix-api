"""Queue and render password reset emails.

The outbox row stores only the user id. The reset token is issued when the message is
actually rendered, so the raw token lives in the email link and not in the database, and
the link is built from the configured FRONTEND_URL rather than any request header.
"""
import secrets
from html import escape
from urllib.parse import quote, urlsplit

from sqlalchemy.orm import Session

from app.api.core.config import settings
from app.api.core.logger import get_logger
from app.api.core.notification_outbox import enqueue_notification
from app.api.core.notification_transport import OutboundMessage
from app.api.crud import crud_password_reset
from app.db.models.notification_outbox import NotificationOutbox
from app.db.models.user import User

KIND = "password_reset"
FALLBACK = "en"
logger = get_logger("password_reset_email")

_TEXTS = {
    "en": (
        "Reset your Xtrix password",
        "Hello {name},\n\nwe received a request to reset your password. "
        "The link is valid for 30 minutes and can be used once:\n\n{link}\n\n"
        "If you did not ask for this, ignore this email; your password stays the same.\n\n{sign}",
        "The Xtrix team",
    ),
    "ru": (
        "Сброс пароля Xtrix",
        "Здравствуйте, {name}!\n\nМы получили запрос на сброс пароля. "
        "Ссылка действует 30 минут и может быть использована один раз:\n\n{link}\n\n"
        "Если вы не запрашивали сброс, просто проигнорируйте письмо; пароль останется прежним.\n\n{sign}",
        "Команда Xtrix",
    ),
    "cz": (
        "Obnovení hesla Xtrix",
        "Dobrý den, {name},\n\nobdrželi jsme žádost o obnovení hesla. "
        "Odkaz je platný 30 minut a lze jej použít pouze jednou:\n\n{link}\n\n"
        "Pokud jste o obnovení nežádali, tento e-mail ignorujte; heslo zůstane stejné.\n\n{sign}",
        "Tým Xtrix",
    ),
}


class PasswordResetEmailError(Exception):
    pass


def _frontend_base() -> str:
    base = (settings.frontend_url or "").strip().rstrip("/")
    parts = urlsplit(base)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise PasswordResetEmailError("frontend_url is not configured")
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


def queue_password_reset(db: Session, user: User, language: str | None = None) -> NotificationOutbox:
    event_key = f"user:{user.id}:password_reset:{secrets.token_hex(12)}"
    return enqueue_notification(
        db,
        event_key=event_key,
        kind=KIND,
        recipient=user.email,
        user_id=user.id,
        language=language if language in _TEXTS else None,
    )


def render_password_reset(db: Session, row: NotificationOutbox) -> OutboundMessage:
    user = db.get(User, row.user_id) if row.user_id else None
    if user is None or user.deleted_at is not None:
        raise PasswordResetEmailError("reset user not found")
    base = _frontend_base()
    raw_token = crud_password_reset.issue_token(db, user.id)[1]
    link = f"{base}/reset-password#token={quote(raw_token, safe='')}"
    language = row.language if row.language in _TEXTS else FALLBACK
    subject, body, sign = _TEXTS[language]
    values = {"name": user.full_name or "", "link": link, "sign": sign}
    subject = subject.format(**values)
    body = body.format(**values)
    logger.info(f"Password reset email prepared for user_id={user.id}")
    return OutboundMessage(
        channel="email",
        recipient=row.recipient,
        subject=subject,
        text=body,
        html=_html(body, link),
    )
