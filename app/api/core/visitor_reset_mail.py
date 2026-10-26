"""Private development capture for account-bound password reset proofs."""
from collections import deque
from dataclasses import dataclass, field
from datetime import timedelta
from threading import Lock
from urllib.parse import urlsplit

from pydantic import EmailStr, TypeAdapter, ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.core import visitor_challenges, visitor_sessions
from app.api.core.clock import utcnow
from app.api.core.config import settings
from app.db.models.visitor_account import VisitorAccount, canonical_visitor_email
from app.db.models.visitor_auth import VisitorChallenge

_messages = deque(maxlen=64)
_capture_lock = Lock()
_UNAVAILABLE = "Visitor password reset mail is unavailable"


class ResetMailUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class PreparedResetMail:
    challenge_id: int = field(repr=False)
    challenge_hash: str = field(repr=False)
    account_id: int = field(repr=False)
    recipient: str = field(repr=False)
    subject: str = field(repr=False)
    text: str = field(repr=False)


def _capture_base() -> str:
    if (settings.visitor_auth_mail_mode != "capture"
            or settings.environment not in {"development", "test"}):
        raise ResetMailUnavailable(_UNAVAILABLE)
    value = settings.frontend_url
    if not isinstance(value, str):
        raise ResetMailUnavailable(_UNAVAILABLE)
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
        raise ResetMailUnavailable(_UNAVAILABLE)
    return f"{parsed.scheme}://{parsed.netloc}"


def _message(locale: str, link: str) -> tuple[str, str]:
    texts = {
        "en": ("Xtrix password reset", "To reset your Xtrix visitor account password, open this link:\n{link}\n\nIf you did not request this, ignore this message."),
        "ru": ("Сброс пароля Xtrix", "Чтобы сбросить пароль аккаунта посетителя Xtrix, откройте ссылку:\n{link}\n\nЕсли вы не запрашивали сброс пароля, проигнорируйте это сообщение."),
        "cz": ("Obnovení hesla Xtrix", "Pro obnovení hesla návštěvnického účtu Xtrix otevřete tento odkaz:\n{link}\n\nPokud jste o obnovení hesla nežádali, zprávu ignorujte."),
    }
    subject, body = texts.get(locale, texts["en"])
    return subject, body.format(link=link)


def prepare(db: Session, email: str, locale: str) -> PreparedResetMail | None:
    """Flush an account-bound reset proof; caller commits or rolls back everything.

    None covers both unknown addresses and throttled accounts. HTTP must always use
    the same generic response. Unknown addresses rely on the route's IP limiter;
    they do not create unbound reset proofs or acquire email advisory locks.
    """
    _capture_base()
    try:
        target = canonical_visitor_email(str(TypeAdapter(EmailStr).validate_python(email)))
    except (ValueError, TypeError, ValidationError):
        raise ValueError("Invalid reset email") from None
    account_id = db.query(VisitorAccount.id).filter(
        VisitorAccount.email_canonical == target, VisitorAccount.email_verified_at.is_not(None),
    ).scalar()
    if account_id is None:
        return None
    try:
        account = visitor_sessions.lock_verified_account(db, account_id)
    except visitor_sessions.VisitorAccountUnavailable:
        return None
    # A rename/delete may have committed while Account was locked elsewhere.
    base = _capture_base()
    if account.email_canonical != target:
        return None
    now = utcnow()
    count, latest = db.query(func.count(VisitorChallenge.id), func.max(VisitorChallenge.created_at)).filter(
        VisitorChallenge.purpose == "reset", VisitorChallenge.account_id == account.id,
        VisitorChallenge.created_at >= now - timedelta(hours=1),
    ).one()
    if count >= 5 or (latest is not None and now - latest < timedelta(seconds=60)):
        return None
    proof = visitor_challenges.issue_challenge(db, "reset", account_id=account.id, email=target, now=now)
    subject, body = _message(locale, base + "/visitor/reset-password#token=" + proof.raw_token)
    _capture_base()
    return PreparedResetMail(proof.row.id, proof.row.token_hash, account.id, target, subject, body)


def capture_after_commit(db: Session, prepared: PreparedResetMail) -> None:
    """Capture only a committed, current proof. This has no live-provider fallback."""
    _capture_base()
    if not isinstance(prepared, PreparedResetMail) or db.in_transaction():
        raise ResetMailUnavailable(_UNAVAILABLE)
    with db.get_bind().connect() as observer:
        observer.exec_driver_sql("SET TRANSACTION READ ONLY")
        row = observer.execute(select(VisitorChallenge.token_hash, VisitorChallenge.expires_at,
            VisitorChallenge.consumed_at, VisitorChallenge.email_canonical).join(
                VisitorAccount, VisitorAccount.id == VisitorChallenge.account_id).where(
                VisitorChallenge.id == prepared.challenge_id, VisitorChallenge.purpose == "reset",
                VisitorChallenge.account_id == prepared.account_id,
                VisitorAccount.email_canonical == prepared.recipient,
                VisitorAccount.email_verified_at.is_not(None))).first()
    if (row is None or row.token_hash != prepared.challenge_hash or row.consumed_at is not None
            or row.expires_at <= utcnow() or row.email_canonical != prepared.recipient):
        raise ResetMailUnavailable(_UNAVAILABLE)
    _capture_base()
    with _capture_lock:
        if not any(item.challenge_id == prepared.challenge_id and item.challenge_hash == prepared.challenge_hash
                   for item in _messages):
            _messages.append(prepared)


def captured_messages() -> tuple[PreparedResetMail, ...]:
    """Private local-check inspection; never expose this through an HTTP route."""
    with _capture_lock:
        return tuple(_messages)


def clear_capture() -> None:
    with _capture_lock:
        _messages.clear()
