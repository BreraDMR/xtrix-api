"""Accept an explicit client invitation in the caller's transaction, without mail."""
import base64
import binascii
from dataclasses import dataclass, field
import hashlib
import re

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.core import visitor_invite_authorization as authority
from app.api.core import visitor_sessions
from app.api.core.clock import utcnow
from app.db.models.business import Business
from app.db.models.client import Client
from app.db.models.visitor_account import VisitorAccount
from app.db.models.visitor_access import VisitorClaim, VisitorGrant

_PATTERN = re.compile(r'vci_[A-Za-z0-9_-]{43}\Z')


class InvalidVisitorSession(ValueError):
    """The exact verified visitor session is no longer available."""


class InvalidClientInvite(ValueError):
    """The invitation or its original fixed target is unavailable."""


class ClientInviteConflict(ValueError):
    """An invitation cannot transfer another visitor's client or visit access."""


class ClientInviteAuthorityDenied(ValueError):
    """The original issuer can no longer invite this recipient to this client."""


class ClientInviteBusy(ValueError):
    """Concurrent permission edits require a full rollback and a retry."""


@dataclass(frozen=True)
class CompletedClientInvite:
    grant_id: int = field(repr=False)
    account_id: int = field(repr=False)
    business_id: int = field(repr=False)
    client_id: int = field(repr=False)
    created: bool


def _digest(raw):
    if not isinstance(raw, str) or _PATTERN.fullmatch(raw) is None:
        raise InvalidClientInvite('Client invitation is not available')
    encoded = raw[4:]
    try:
        decoded = base64.b64decode(encoded + '=', altchars=b'-_', validate=True)
    except (ValueError, binascii.Error):
        raise InvalidClientInvite('Client invitation is not available') from None
    if len(decoded) != 32 or base64.urlsafe_b64encode(decoded).decode('ascii').rstrip('=') != encoded:
        raise InvalidClientInvite('Client invitation is not available')
    return hashlib.sha256(raw.encode('ascii')).hexdigest()


def _session(db, raw, account_id, session_id):
    current = visitor_sessions.get_valid_session(db, raw)
    if (current is None or current.account_id != account_id or current.id != session_id
            or current.expires_at <= utcnow()):
        raise InvalidVisitorSession('Visitor session is not available')


def _target(db, business_id, client_id):
    business = db.query(Business.id).filter(Business.id == business_id, Business.deleted_at.is_(None)).scalar()
    client = db.query(Client.id).filter(Client.id == client_id, Client.deleted_at.is_(None)).scalar()
    if business is None or client is None:
        raise InvalidClientInvite('Client invitation is not available')


def _fixed(receipt, digest, claim_id, business_id, client_id):
    if (receipt is None or receipt.id != claim_id or receipt.proof_hash != digest
            or receipt.kind != 'client_invite' or receipt.business_id != business_id
            or receipt.client_id != client_id or receipt.revoked_at is not None
            or receipt.visit_id is not None or receipt.source_manage_token_id is not None):
        raise InvalidClientInvite('Client invitation is not available')


def _pending(db, receipt, account_id, now):
    email = db.query(VisitorAccount.email_canonical).filter(
        VisitorAccount.id == account_id, VisitorAccount.email_verified_at.is_not(None)).scalar()
    if (receipt.consumed_at is not None or receipt.expires_at <= now or receipt.created_at > now
            or email is None or receipt.recipient_email != email):
        raise InvalidClientInvite('Client invitation is not available')


def _authorize(db, issuer_id, business_id, client_id):
    try:
        authority.authorize_client_invite(db, issuer_user_id=issuer_id,
                                          business_id=business_id, client_id=client_id)
    except authority.InviteAuthorizationBusy:
        raise ClientInviteBusy('Invitation authority is busy; retry after rollback') from None
    except authority.InviteAuthorizationDenied:
        raise ClientInviteAuthorityDenied('Invitation authority is not available') from None


def accept_client_invite(db: Session, raw_visitor_session: str | None,
                         raw_invite: str | None) -> CompletedClientInvite:
    """Flush one recipient-bound client grant. Every error needs whole rollback.

    Account -> Client -> Claim -> Grant are blocking locks. Supporting issuer locks
    are NOWAIT, before any grant/FK writes and again after the last blocking work.
    An owned spent receipt resolves only its original live grant, without requiring
    its old issuer, recipient address, membership or invitation lifetime to survive.
    """
    digest = _digest(raw_invite)
    with db.no_autoflush:
        first = visitor_sessions.get_valid_session(db, raw_visitor_session)
        if first is None:
            raise InvalidVisitorSession('Visitor session is not available')
        account_id, session_id = first.account_id, first.id
        hint = db.query(VisitorClaim.id, VisitorClaim.kind, VisitorClaim.business_id,
                        VisitorClaim.client_id).filter(VisitorClaim.proof_hash == digest).one_or_none()
        if hint is None or hint.kind != 'client_invite':
            raise InvalidClientInvite('Client invitation is not available')
        claim_id, business_id, client_id = hint.id, hint.business_id, hint.client_id
        try:
            visitor_sessions.lock_verified_account(db, account_id)
        except visitor_sessions.VisitorAccountUnavailable:
            raise InvalidVisitorSession('Visitor session is not available') from None
        _session(db, raw_visitor_session, account_id, session_id)
        client = db.query(Client.id).filter(Client.id == client_id, Client.deleted_at.is_(None)
            ).with_for_update(key_share=True).scalar()
        if client is None:
            raise InvalidClientInvite('Client invitation is not available')
        # Re-read under the Client anchor: another acceptance may have finished while
        # this request waited. Spent replay must not depend on an expired issuer.
        fresh = db.query(VisitorClaim.id, VisitorClaim.kind, VisitorClaim.business_id,
                         VisitorClaim.client_id, VisitorClaim.consumed_at,
                         VisitorClaim.issued_by_user_id).filter(VisitorClaim.proof_hash == digest).one_or_none()
        if (fresh is None or fresh.id != claim_id or fresh.kind != 'client_invite'
                or fresh.business_id != business_id or fresh.client_id != client_id):
            raise InvalidClientInvite('Client invitation is not available')
        initial_issuer = None
        if fresh.consumed_at is None:
            initial_issuer = fresh.issued_by_user_id
            _authorize(db, initial_issuer, business_id, client_id)
        receipt = db.query(VisitorClaim).filter(VisitorClaim.id == claim_id, VisitorClaim.proof_hash == digest
            ).populate_existing().with_for_update().one_or_none()
        _fixed(receipt, digest, claim_id, business_id, client_id)
        _target(db, business_id, client_id)
        _session(db, raw_visitor_session, account_id, session_id)
        if receipt.consumed_at is not None:
            if receipt.consumed_account_id is not None and receipt.consumed_account_id != account_id:
                raise ClientInviteConflict('Client invitation is already claimed')
            if receipt.consumed_account_id is None or receipt.grant_id is None:
                raise InvalidClientInvite('Client invitation is not available')
            grant = db.query(VisitorGrant).filter(
                VisitorGrant.id == receipt.grant_id, VisitorGrant.account_id == account_id,
                VisitorGrant.business_id == business_id, VisitorGrant.client_id == client_id,
                VisitorGrant.scope == 'client', VisitorGrant.visit_id.is_(None),
                VisitorGrant.revoked_at.is_(None)).populate_existing().with_for_update().one_or_none()
            if grant is None:
                raise InvalidClientInvite('Client invitation is not available')
            _target(db, business_id, client_id)
            _session(db, raw_visitor_session, account_id, session_id)
            return CompletedClientInvite(grant.id, account_id, business_id, client_id, False)
        if receipt.issued_by_user_id != initial_issuer or initial_issuer is None:
            _authorize(db, receipt.issued_by_user_id, business_id, client_id)
        _pending(db, receipt, account_id, utcnow())
        grants = db.query(VisitorGrant).filter(VisitorGrant.business_id == business_id,
            VisitorGrant.client_id == client_id, VisitorGrant.revoked_at.is_(None)
            ).order_by(VisitorGrant.id).populate_existing().with_for_update().all()
        if any(grant.account_id != account_id for grant in grants):
            raise ClientInviteConflict('Client target is already claimed')
        grant = next((row for row in grants if row.scope == 'client'), None)
        created = grant is None
        if created:
            grant = VisitorGrant(account_id=account_id, business_id=business_id,
                client_id=client_id, scope='client', visit_id=None, created_at=utcnow())
            db.add(grant)
        try:
            db.flush()
            receipt.consumed_at = utcnow()
            receipt.consumed_account_id, receipt.grant_id = account_id, grant.id
            db.flush()
        except IntegrityError as exc:
            name = getattr(getattr(exc.orig, 'diag', None), 'constraint_name', None)
            if name in {'uq_visitor_grant_active_client', 'uq_visitor_claims_proof_hash'}:
                raise ClientInviteConflict('Client target is already claimed') from None
            raise
        _authorize(db, receipt.issued_by_user_id, business_id, client_id)
        _target(db, business_id, client_id)
        _session(db, raw_visitor_session, account_id, session_id)
        now = utcnow()
        # The receipt has been flushed, so validate its pending-proof constraints
        # without treating our own uncommitted consumption as a replay.
        email = db.query(VisitorAccount.email_canonical).filter(
            VisitorAccount.id == account_id, VisitorAccount.email_verified_at.is_not(None)).scalar()
        if receipt.expires_at <= now or receipt.created_at > now or email != receipt.recipient_email:
            raise InvalidClientInvite('Client invitation is not available')
        receipt.consumed_at = now
        db.flush()
        # The timestamp-only UPDATE adds no new FK/unique wait in the current schema.
        # Late expiry still fails closed; future triggers are not assumed harmless.
        _session(db, raw_visitor_session, account_id, session_id)
        if receipt.expires_at <= utcnow():
            raise InvalidClientInvite('Client invitation is not available')
        return CompletedClientInvite(grant.id, account_id, business_id, client_id, created)
