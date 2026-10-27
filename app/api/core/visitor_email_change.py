"""Confirm a visitor's new email and revoke credentials in the caller's transaction."""
from dataclasses import dataclass, field
from datetime import datetime

from pydantic import EmailStr, TypeAdapter
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.core import visitor_challenges, visitor_recent_auth, visitor_sessions
from app.api.core.clock import utcnow
from app.db.models.visitor_account import VisitorAccount, canonical_visitor_email
from app.db.models.visitor_auth import VisitorChallenge, VisitorSession


class InvalidEmailChangeRecipient(ValueError):
    """The explicit new contact is not a valid email address."""


class InvalidEmailChangeProof(ValueError):
    """This email proof is unavailable, expired or belongs to another purpose/account."""


class EmailChangeConflict(ValueError):
    """The requested change cannot be completed; no other identity is disclosed."""


@dataclass(frozen=True)
class PreparedEmailChange:
    account_id: int = field(repr=False)
    challenge_id: int = field(repr=False)
    raw_token: str = field(repr=False)
    expires_at: datetime = field(repr=False)


@dataclass(frozen=True)
class CompletedEmailChange:
    account_id: int = field(repr=False)
    revoked_sessions: int


def _recipient(value) -> str:
    try:
        return canonical_visitor_email(str(TypeAdapter(EmailStr).validate_python(value)))
    except (ValueError, TypeError):
        raise InvalidEmailChangeRecipient('A valid new email is required') from None


def _authentication_deadline(db, actor):
    row = db.query(VisitorSession.created_at, VisitorSession.expires_at).filter(
        VisitorSession.id == actor.session_id, VisitorSession.account_id == actor.account_id,
        VisitorSession.revoked_at.is_(None)).one_or_none()
    now = utcnow()
    if row is None or row.created_at > now or row.expires_at <= now:
        raise visitor_recent_auth.VisitorReauthenticationRequired('A recent visitor login is required')
    return min(row.created_at + visitor_recent_auth.RECENT_AUTH_TTL, row.expires_at)


def _require_deadline(deadline, proof_expires_at):
    # Deliberate logout makes normal session authentication fail. These immutable
    # deadlines still let us reject time spent waiting on revocation/sibling writes.
    now = utcnow()
    if now >= proof_expires_at:
        raise InvalidEmailChangeProof('Email change proof is not available')
    if now >= deadline:
        raise visitor_recent_auth.VisitorReauthenticationRequired('A recent visitor login is required')


def issue(db: Session, raw_visitor_session: str | None, new_email: str) -> PreparedEmailChange:
    """Flush a bound proof, without mail, commit or a lookup of the new recipient."""
    recipient = _recipient(new_email)
    with db.no_autoflush:
        actor = visitor_recent_auth.lock_recent_actor(db, raw_visitor_session)
        deadline = _authentication_deadline(db, actor)
        issued = visitor_challenges.issue_challenge(db, 'email_change', account_id=actor.account_id,
                                                    email=recipient)
        visitor_recent_auth.require_recent_actor(db, raw_visitor_session, actor)
        _require_deadline(min(deadline, issued.row.expires_at), issued.row.expires_at)
        return PreparedEmailChange(actor.account_id, issued.row.id, issued.raw_token, issued.row.expires_at)


def confirm(db: Session, raw_visitor_session: str | None,
            raw_proof: str | None) -> CompletedEmailChange:
    """Change only the contact, consuming proofs and revoking all visitor sessions.

    Account -> Challenge -> Sessions -> sibling challenges matches password reset.
    Caller must whole-rollback every error, including a late expired deadline or a
    unique email conflict. Nothing creates a session or transfers a Google principal.
    """
    with db.no_autoflush:
        actor = visitor_recent_auth.lock_recent_actor(db, raw_visitor_session)
        deadline = _authentication_deadline(db, actor)
        proof = visitor_challenges.consume_challenge(db, raw_proof, purpose='email_change',
                                                     account_id=actor.account_id)
        if proof is None:
            raise InvalidEmailChangeProof('Email change proof is not available')
        proof_expires_at = proof.expires_at
        deadline = min(deadline, proof_expires_at)
        try:
            recipient = _recipient(proof.email_canonical)
        except InvalidEmailChangeRecipient:
            raise InvalidEmailChangeProof('Email change proof is not available') from None
        visitor_recent_auth.require_recent_actor(db, raw_visitor_session, actor)
        _require_deadline(deadline, proof_expires_at)
        account = db.get(VisitorAccount, actor.account_id)
        account.email_canonical = recipient
        account.email_verified_at = utcnow()
        try:
            db.flush()
        except IntegrityError as exc:
            name = getattr(getattr(exc.orig, 'diag', None), 'constraint_name', None)
            if name == 'uq_visitor_accounts_email_canonical':
                raise EmailChangeConflict('Email change could not be completed') from None
            raise
        # The unique index may have waited for another transaction. Authenticate
        # again while this credential is still intentionally usable.
        visitor_recent_auth.require_recent_actor(db, raw_visitor_session, actor)
        _require_deadline(deadline, proof_expires_at)
        revoked = visitor_sessions.revoke_all_sessions(db, account_id=actor.account_id)
        db.query(VisitorChallenge).filter(
            VisitorChallenge.account_id == actor.account_id,
            VisitorChallenge.purpose.in_(('reset', 'email_change')),
            VisitorChallenge.consumed_at.is_(None),
        ).update({'consumed_at': utcnow()}, synchronize_session='fetch')
        db.flush()
        # Do not call require_recent_actor after deliberately revoking its session.
        _require_deadline(deadline, proof_expires_at)
        return CompletedEmailChange(actor.account_id, revoked)
