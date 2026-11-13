"""Durable visitor proof mail: register, password reset and email change links.

In "live" mode the request transaction stores the challenge and an encrypted copy of its
raw link token together. The request then tries to send right after commit; whatever
didn't go out is retried by the scheduled worker (run_jobs.py visitor_mail) until the
proof expires. The token is AES-GCM encrypted with a key from VISITOR_PROOF_MAIL_KEYS and
bound to the challenge row, and it is wiped as soon as the job is done one way or another.

No exactly-once promise: a crash after the provider accepted the mail but before we
recorded it means the same link can arrive twice. It never makes a new link.
"""
from base64 import urlsafe_b64decode
from dataclasses import dataclass
from datetime import timedelta
import hashlib
import hmac
import importlib
import os
import uuid

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.api.core.clock import utcnow
from app.api.core.config import settings
from app.api.core.logger import logger
from app.db.models.visitor_account import VisitorAccount
from app.db.models.visitor_auth import VisitorChallenge
from app.db.models.visitor_proof_mail_job import VisitorProofMailJob as Job

# purpose -> (module that owns the wording, link path in the web client)
_PURPOSES = {
    'register': ('app.api.core.visitor_registration_mail', '/visitor/register#token='),
    'reset': ('app.api.core.visitor_reset_mail', '/visitor/reset-password#token='),
    'email_change': ('app.api.core.visitor_email_change_mail', '/visitor/email-change#token='),
}
_MAX_ATTEMPTS = 5
_LEASE = timedelta(minutes=2)
_AAD_VERSION = b'xtrix.visitor-proof-mail.v1'


class ProofMailKeyUnavailable(RuntimeError):
    """Live mode without a usable key: refuse the request instead of storing the link in clear."""


@dataclass(frozen=True)
class _Key:
    key_id: str
    secret: bytes


def live_enabled() -> bool:
    return settings.visitor_auth_mail_mode == 'live'


def _keyring() -> list[_Key]:
    raw = settings.visitor_proof_mail_keys.get_secret_value().strip()
    keys = []
    for part in filter(None, (piece.strip() for piece in raw.split(','))):
        key_id, sep, encoded = part.partition(':')
        try:
            secret = urlsafe_b64decode(encoded + '=' * (-len(encoded) % 4)) if sep else b''
        except ValueError:
            secret = b''
        if not key_id or len(key_id) > 32 or len(secret) != 32:
            raise ProofMailKeyUnavailable('VISITOR_PROOF_MAIL_KEYS is malformed')
        keys.append(_Key(key_id, secret))
    if not keys:
        raise ProofMailKeyUnavailable('VISITOR_PROOF_MAIL_KEYS is empty')
    return keys


def _aesgcm(secret):
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    return AESGCM(secret)


def _aad(key_id, challenge_id, purpose, token_hash):
    # ties the ciphertext to exactly one challenge row; a copied blob won't decrypt elsewhere
    return b'|'.join((_AAD_VERSION, key_id.encode(), str(challenge_id).encode(), purpose.encode(), token_hash.encode()))


def check_ready() -> None:
    """Fail before anything is written when live mode can't encrypt."""
    _keyring()


def enqueue(db: Session, challenge_id: int, raw_token: str, locale: str, origin: str) -> int:
    """Add the job inside the caller's transaction, right after the challenge was flushed."""
    challenge = db.execute(select(VisitorChallenge.id, VisitorChallenge.purpose, VisitorChallenge.token_hash,
        VisitorChallenge.expires_at).where(VisitorChallenge.id == challenge_id)).one()
    if challenge.purpose not in _PURPOSES:
        raise ValueError('No proof mail for this purpose')
    if hashlib.sha256(raw_token.encode('ascii')).hexdigest() != challenge.token_hash:
        raise ValueError('Token does not belong to this challenge')
    key = _keyring()[0]
    nonce = os.urandom(12)
    ciphertext = _aesgcm(key.secret).encrypt(nonce, raw_token.encode('ascii'),
        _aad(key.key_id, challenge.id, challenge.purpose, challenge.token_hash))
    now = utcnow()
    job = Job(challenge_id=challenge.id, purpose=challenge.purpose, locale=locale if locale in ('en', 'ru', 'cz') else 'en',
              origin=origin, key_id=key.key_id, nonce=nonce, ciphertext=ciphertext, status='pending', attempts=0,
              next_attempt_at=now, deadline=challenge.expires_at, created_at=now, updated_at=now)
    db.add(job)
    db.flush()
    return job.id


def _finish(db, job_id, ticket, status, error=None):
    db.execute(update(Job).where(Job.id == job_id, Job.lease_ticket == ticket, Job.status == 'sending').values(
        status=status, key_id=None, nonce=None, ciphertext=None, next_attempt_at=None, lease_until=None,
        lease_ticket=None, last_error=error, updated_at=utcnow()))
    db.commit()


def _retry_later(db, job_id, ticket, attempts, deadline, error):
    now = utcnow()
    if attempts >= _MAX_ATTEMPTS or now >= deadline:
        return _finish(db, job_id, ticket, 'failed' if now < deadline else 'expired', error)
    delay = timedelta(seconds=30 * 2 ** (attempts - 1))
    db.execute(update(Job).where(Job.id == job_id, Job.lease_ticket == ticket, Job.status == 'sending').values(
        status='pending', next_attempt_at=min(deadline, now + delay), lease_until=None, lease_ticket=None,
        last_error=error, updated_at=now))
    db.commit()


def _claim(db, job_id=None):
    now = utcnow()
    query = select(Job).where(((Job.status == 'pending') & (Job.next_attempt_at <= now))
                              | ((Job.status == 'sending') & (Job.lease_until < now)))
    if job_id is not None:
        query = query.where(Job.id == job_id)
    job = db.execute(query.order_by(Job.next_attempt_at, Job.id).with_for_update(skip_locked=True).limit(1)
                     ).scalar_one_or_none()
    if job is None:
        db.rollback()
        return None
    ticket = uuid.uuid4().hex
    job.status, job.lease_ticket, job.lease_until = 'sending', ticket, now + _LEASE
    job.next_attempt_at = None
    job.attempts += 1
    job.updated_at = now
    claimed = (job.id, ticket, job.attempts, job.deadline, job.challenge_id, job.purpose, job.locale,
               job.origin, job.key_id, job.nonce, job.ciphertext)
    db.commit()
    return claimed


def _recipient_or_none(db, challenge, purpose):
    """Who may still get this link right now, or None when the proof became useless."""
    if challenge is None or challenge.consumed_at is not None or challenge.expires_at <= utcnow():
        return None
    if challenge.purpose != purpose or not challenge.email_canonical:
        return None
    if purpose in ('reset', 'email_change'):
        account = db.get(VisitorAccount, challenge.account_id) if challenge.account_id else None
        if account is None or account.email_verified_at is None:
            return None
        # a reset goes only to the address the account still has
        if purpose == 'reset' and account.email_canonical != challenge.email_canonical:
            return None
    return challenge.email_canonical


def _deliver(db, claimed, transport):
    job_id, ticket, attempts, deadline, challenge_id, purpose, locale, origin, key_id, nonce, ciphertext = claimed
    challenge = db.get(VisitorChallenge, challenge_id) if challenge_id else None
    recipient = _recipient_or_none(db, challenge, purpose)
    if recipient is None:
        expired = challenge is not None and challenge.expires_at <= utcnow()
        return _finish(db, job_id, ticket, 'expired' if expired else 'cancelled')
    keys = {key.key_id: key for key in _keyring()}
    key = keys.get(key_id)
    if key is None:
        # the key was rotated out too early; this link can't be recovered, a new request makes a new one
        return _finish(db, job_id, ticket, 'failed', 'key_unavailable')
    try:
        raw = _aesgcm(key.secret).decrypt(nonce, ciphertext, _aad(key_id, challenge.id, purpose, challenge.token_hash))
        token = raw.decode('ascii')
    except Exception:
        return _finish(db, job_id, ticket, 'failed', 'decrypt_failed')
    if not hmac.compare_digest(hashlib.sha256(token.encode('ascii')).hexdigest(), challenge.token_hash):
        return _finish(db, job_id, ticket, 'failed', 'proof_mismatch')
    module_name, path = _PURPOSES[purpose]
    subject, text = importlib.import_module(module_name)._message(locale, origin + path + token)
    db.rollback()  # no locks or snapshot held while we talk to the provider
    from app.api.core.notification_transport import OutboundMessage
    try:
        result = transport.send(OutboundMessage(channel='email', recipient=recipient, text=text, subject=subject))
    except Exception as exc:
        return _retry_later(db, job_id, ticket, attempts, deadline, f'transport_{type(exc).__name__}'[:40])
    if result.status in ('sent', 'captured'):
        return _finish(db, job_id, ticket, result.status)
    return _retry_later(db, job_id, ticket, attempts, deadline, (result.error or result.status or 'failed')[:40])


def run_once(db: Session, *, limit=50, transport=None, job_id=None) -> dict:
    if db.new or db.dirty or db.deleted:
        raise ValueError('Save pending changes before running proof mail jobs')
    if transport is None:
        from app.api.core.notification_transport import get_notification_transport
        transport = get_notification_transport()
    stats = {'handled': 0}
    for _ in range(limit):
        claimed = _claim(db, job_id)
        if claimed is None:
            break
        try:
            _deliver(db, claimed, transport)
        except Exception as exc:
            db.rollback()
            logger.error('visitor proof mail job %s failed: %s', claimed[0], type(exc).__name__)
            _retry_later(db, claimed[0], claimed[1], claimed[2], claimed[3], 'runner_error')
        stats['handled'] += 1
        if job_id is not None:
            break
    db.rollback()
    return stats


def deliver_after_commit(db: Session, job_id: int) -> None:
    """Best-effort immediate send for a request; the worker picks up anything that fails here."""
    try:
        run_once(db, job_id=job_id)
    except Exception as exc:
        db.rollback()
        logger.warning('visitor proof mail %s left for the worker: %s', job_id, type(exc).__name__)


def run_scheduled(limit=200) -> dict:
    from app.db.session import SessionLocal
    db = SessionLocal()
    try:
        return run_once(db, limit=limit)
    finally:
        db.close()
