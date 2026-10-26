"""Private development capture for registration proofs; never a live transport."""
from collections import deque
from dataclasses import dataclass, field
from datetime import timedelta
import hashlib
from threading import Lock
from urllib.parse import urlsplit

from pydantic import EmailStr, TypeAdapter, ValidationError
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.api.core import visitor_challenges
from app.api.core.clock import utcnow
from app.api.core.config import settings
from app.db.models.visitor_account import canonical_visitor_email
from app.db.models.visitor_auth import VisitorChallenge

_CAPTURE_SIZE = 64
_messages = deque(maxlen=_CAPTURE_SIZE)
_capture_lock = Lock()
_UNAVAILABLE = "Visitor registration mail is unavailable"


class RegistrationMailUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class PreparedRegistrationMail:
    challenge_id: int = field(repr=False)
    challenge_hash: str = field(repr=False)
    recipient: str = field(repr=False)
    subject: str = field(repr=False)
    text: str = field(repr=False)


def _capture_base() -> str:
    if (settings.visitor_auth_mail_mode != "capture"
            or settings.environment not in {"development", "test"}):
        raise RegistrationMailUnavailable(_UNAVAILABLE)
    value = settings.frontend_url
    if not isinstance(value, str):
        raise RegistrationMailUnavailable(_UNAVAILABLE)
    try:
        parsed = urlsplit(value)
        parsed.port
        valid = (not any(c.isspace() for c in value)
                 and parsed.scheme in {"http", "https"} and bool(parsed.hostname)
                 and parsed.username is None and parsed.password is None
                 and not parsed.query and not parsed.fragment and parsed.path in {"", "/"}
                 and value in {f"{parsed.scheme}://{parsed.netloc}", f"{parsed.scheme}://{parsed.netloc}/"})
    except (TypeError, ValueError):
        valid = False
    if not valid:
        raise RegistrationMailUnavailable(_UNAVAILABLE)
    return f"{parsed.scheme}://{parsed.netloc}"


def _message(locale: str, link: str) -> tuple[str, str]:
    texts = {
        "en": ("Xtrix registration", "To complete your Xtrix registration, open this link:\n{link}\n\nIf you did not request this, ignore this message."),
        "ru": ("Регистрация Xtrix", "Чтобы завершить регистрацию Xtrix, откройте ссылку:\n{link}\n\nЕсли вы не запрашивали регистрацию, проигнорируйте это сообщение."),
        "cz": ("Registrace Xtrix", "Pro dokončení registrace Xtrix otevřete tento odkaz:\n{link}\n\nPokud jste o registraci nežádali, zprávu ignorujte."),
    }
    subject, body = texts.get(locale, texts["en"])
    return subject, body.format(link=link)


def prepare(db: Session, email: str, locale: str) -> PreparedRegistrationMail | None:
    """Flush a digest, never an account or delivery. Caller must commit before capture.

    The advisory key is only for this anonymous request path, which never locks
    an Account. All valid addresses follow this same path, including existing ones.
    """
    base = _capture_base()
    try:
        target = canonical_visitor_email(str(TypeAdapter(EmailStr).validate_python(email)))
    except (ValueError, TypeError, ValidationError):
        raise ValueError("Invalid registration email") from None
    key = int.from_bytes(hashlib.sha256(b"xtrix:visitor-register-request:" + target.encode("utf-8")).digest()[:8],
                         "big", signed=True)
    db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": key})
    now = utcnow()
    count, latest = db.query(func.count(VisitorChallenge.id), func.max(VisitorChallenge.created_at)).filter(
        VisitorChallenge.purpose == "register", VisitorChallenge.email_canonical == target,
        VisitorChallenge.created_at >= now - timedelta(hours=1),
    ).one()
    if count >= 5 or (latest is not None and now - latest < timedelta(seconds=60)):
        return None
    proof = visitor_challenges.issue_challenge(db, "register", email=target, now=now)
    subject, body = _message(locale, base + "/visitor/register#token=" + proof.raw_token)
    return PreparedRegistrationMail(proof.row.id, proof.row.token_hash, target, subject, body)


def capture_after_commit(db: Session, prepared: PreparedRegistrationMail) -> None:
    """Capture only a proof visible to another connection after caller commit.

    This API has no live provider fallback, files, logs or public inspection route.
    Call immediately after commit, before another ORM read starts a transaction.
    """
    _capture_base()
    if not isinstance(prepared, PreparedRegistrationMail) or db.in_transaction():
        raise RegistrationMailUnavailable(_UNAVAILABLE)
    with db.get_bind().connect() as observer:
        observer.exec_driver_sql("SET TRANSACTION READ ONLY")
        row = observer.execute(select(VisitorChallenge.token_hash, VisitorChallenge.expires_at,
            VisitorChallenge.consumed_at, VisitorChallenge.email_canonical).where(
                VisitorChallenge.id == prepared.challenge_id,
                VisitorChallenge.purpose == "register")).first()
    if (row is None or row.token_hash != prepared.challenge_hash or row.consumed_at is not None
            or row.expires_at <= utcnow() or row.email_canonical != prepared.recipient):
        raise RegistrationMailUnavailable(_UNAVAILABLE)
    with _capture_lock:
        if not any(item.challenge_id == prepared.challenge_id and item.challenge_hash == prepared.challenge_hash
                   for item in _messages):
            _messages.append(prepared)


def captured_messages() -> tuple[PreparedRegistrationMail, ...]:
    """Private local-check inspection; this must never be exposed by an HTTP route."""
    with _capture_lock:
        return tuple(_messages)


def clear_capture() -> None:
    with _capture_lock:
        _messages.clear()
