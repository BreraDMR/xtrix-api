"""Private local capture for visitor email-change proofs, never a delivery provider."""
from collections import deque
from dataclasses import dataclass, field
from datetime import timedelta
import hashlib
from threading import Lock
from urllib.parse import urlsplit

from pydantic import EmailStr, TypeAdapter
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.core import visitor_email_change, visitor_recent_auth
from app.api.core.clock import utcnow
from app.api.core.config import settings
from app.db.models.visitor_account import VisitorAccount, canonical_visitor_email
from app.db.models.visitor_auth import VisitorChallenge

_messages = deque(maxlen=64)
_capture_lock = Lock()
_UNAVAILABLE = 'Visitor email change mail is unavailable'


class EmailChangeMailUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class PreparedEmailChangeMail:
    challenge_id: int = field(repr=False)
    challenge_hash: str = field(repr=False)
    account_id: int = field(repr=False)
    recipient: str = field(repr=False)
    subject: str = field(repr=False)
    text: str = field(repr=False)


def _capture_base() -> str:
    if (settings.visitor_auth_mail_mode != 'capture'
            or settings.environment not in {'development', 'test'}):
        raise EmailChangeMailUnavailable(_UNAVAILABLE)
    value = settings.frontend_url
    if not isinstance(value, str):
        raise EmailChangeMailUnavailable(_UNAVAILABLE)
    try:
        parsed = urlsplit(value)
        parsed.port
        valid = (not any(c.isspace() for c in value)
                 and parsed.scheme in {'http', 'https'} and bool(parsed.hostname)
                 and parsed.username is None and parsed.password is None
                 and not parsed.query and not parsed.fragment and parsed.path in {'', '/'}
                 and value in {f'{parsed.scheme}://{parsed.netloc}', f'{parsed.scheme}://{parsed.netloc}/'})
    except (TypeError, ValueError):
        valid = False
    if not valid:
        raise EmailChangeMailUnavailable(_UNAVAILABLE)
    return f'{parsed.scheme}://{parsed.netloc}'


def _message(locale: str, link: str) -> tuple[str, str]:
    texts = {
        'en': ('Xtrix email change', 'To confirm the new email for your Xtrix visitor account, open this link:\n{link}\n\nIf you did not request this, ignore this message.'),
        'ru': ('Смена почты Xtrix', 'Чтобы подтвердить новую почту аккаунта посетителя Xtrix, откройте ссылку:\n{link}\n\nЕсли вы не запрашивали смену почты, проигнорируйте это сообщение.'),
        'cz': ('Změna e-mailu Xtrix', 'Pro potvrzení nového e-mailu návštěvnického účtu Xtrix otevřete tento odkaz:\n{link}\n\nPokud jste o změnu e-mailu nežádali, zprávu ignorujte.'),
    }
    subject, body = texts.get(locale, texts['en'])
    return subject, body.format(link=link)


def prepare(db: Session, raw_visitor_session: str | None, new_email: str,
            locale: str) -> PreparedEmailChangeMail | None:
    """Flush a private proof, serializing request limits by Account, never recipient.

    Caller commits before capture or whole-rolls back every preparation error. None
    is throttling, not delivery retry or evidence about another account's address.
    """
    _capture_base()
    try:
        target = canonical_visitor_email(str(TypeAdapter(EmailStr).validate_python(new_email)))
    except (ValueError, TypeError):
        raise visitor_email_change.InvalidEmailChangeRecipient('A valid new email is required') from None
    with db.no_autoflush:
        actor = visitor_recent_auth.lock_recent_actor(db, raw_visitor_session)
        base = _capture_base()
        now = utcnow()
        count, latest = db.query(func.count(VisitorChallenge.id), func.max(VisitorChallenge.created_at)).filter(
            VisitorChallenge.purpose == 'email_change', VisitorChallenge.account_id == actor.account_id,
            VisitorChallenge.created_at >= now - timedelta(hours=1),
        ).one()
        visitor_recent_auth.require_recent_actor(db, raw_visitor_session, actor)
        _capture_base()
        if count >= 5 or (latest is not None and now - latest < timedelta(seconds=60)):
            return None
        issued = visitor_email_change.issue(db, raw_visitor_session, target)
        row = db.query(VisitorChallenge.token_hash, VisitorChallenge.email_canonical).filter(
            VisitorChallenge.id == issued.challenge_id, VisitorChallenge.account_id == actor.account_id,
            VisitorChallenge.purpose == 'email_change').one_or_none()
        expected_hash = hashlib.sha256(issued.raw_token.encode('ascii')).hexdigest()
        if (issued.account_id != actor.account_id or row is None or row.token_hash != expected_hash
                or row.email_canonical != target):
            raise EmailChangeMailUnavailable(_UNAVAILABLE)
        subject, body = _message(locale, base + '/visitor/email-change#token=' + issued.raw_token)
        visitor_recent_auth.require_recent_actor(db, raw_visitor_session, actor)
        _capture_base()
        return PreparedEmailChangeMail(issued.challenge_id, row.token_hash, actor.account_id,
                                       row.email_canonical, subject, body)


def capture_after_commit(db: Session, prepared: PreparedEmailChangeMail) -> None:
    """Observe a committed, current proof, then capture it once in bounded local RAM.

    The target is the proof's new recipient. The Account still has its old email;
    requiring equality there would silently prevent every actual email change.
    """
    _capture_base()
    if not isinstance(prepared, PreparedEmailChangeMail) or db.in_transaction():
        raise EmailChangeMailUnavailable(_UNAVAILABLE)
    with db.get_bind().connect() as observer:
        observer.exec_driver_sql('SET TRANSACTION READ ONLY')
        row = observer.execute(select(VisitorChallenge.token_hash, VisitorChallenge.expires_at,
            VisitorChallenge.consumed_at, VisitorChallenge.email_canonical).join(
                VisitorAccount, VisitorAccount.id == VisitorChallenge.account_id).where(
                VisitorChallenge.id == prepared.challenge_id, VisitorChallenge.purpose == 'email_change',
                VisitorChallenge.account_id == prepared.account_id,
                VisitorAccount.email_verified_at.is_not(None))).first()
    if (row is None or row.token_hash != prepared.challenge_hash or row.consumed_at is not None
            or row.expires_at <= utcnow() or row.email_canonical != prepared.recipient):
        raise EmailChangeMailUnavailable(_UNAVAILABLE)
    with _capture_lock:
        _capture_base()
        if row.expires_at <= utcnow():
            raise EmailChangeMailUnavailable(_UNAVAILABLE)
        if not any(item.challenge_id == prepared.challenge_id and item.challenge_hash == prepared.challenge_hash
                   for item in _messages):
            try:
                _messages.append(prepared)
            except Exception:
                raise EmailChangeMailUnavailable(_UNAVAILABLE) from None


def captured_messages() -> tuple[PreparedEmailChangeMail, ...]:
    """Private local-check inspection; never expose through an HTTP route."""
    with _capture_lock:
        return tuple(_messages)


def clear_capture() -> None:
    with _capture_lock:
        _messages.clear()
