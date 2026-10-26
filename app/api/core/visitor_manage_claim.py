"""Attach one guest-managed visit to a visitor, without consuming the legacy link."""
from dataclasses import dataclass, field
import hashlib

from sqlalchemy import and_, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.core import visitor_sessions
from app.api.core.clock import utcnow
from app.db.models.booking_manage_token import BookingManageToken
from app.db.models.business import Business
from app.db.models.client import Client
from app.db.models.visit import Visit
from app.db.models.visitor_access import VisitorClaim, VisitorGrant


class InvalidVisitorSession(ValueError):
    """A current opaque visitor session is required."""


class InvalidManageClaim(ValueError):
    """The guest proof or its fixed target is no longer available."""


class ManageClaimConflict(ValueError):
    """A guest proof cannot transfer access from another visitor account."""


@dataclass(frozen=True)
class CompletedManageClaim:
    grant_id: int = field(repr=False)
    account_id: int = field(repr=False)
    business_id: int = field(repr=False)
    client_id: int = field(repr=False)
    visit_id: int = field(repr=False)
    created: bool


def _digest(raw: str | None) -> str:
    if not isinstance(raw, str) or not raw or len(raw) > 128:
        raise InvalidManageClaim('Guest proof is not available')
    try:
        return hashlib.sha256(raw.encode('utf-8')).hexdigest()
    except UnicodeError:
        raise InvalidManageClaim('Guest proof is not available') from None


def _require_session(db, raw, account_id, session_id):
    current = visitor_sessions.get_valid_session(db, raw)
    if (current is None or current.account_id != account_id or current.id != session_id
            or current.expires_at <= utcnow()):
        raise InvalidVisitorSession('Visitor session is not available')


def _target(db, visit_id, business_id, client_id):
    visit = db.query(Visit.id, Visit.business_id, Visit.client_id).filter(
        Visit.id == visit_id, Visit.deleted_at.is_(None)).one_or_none()
    business_exists = db.query(Business.id).filter(
        Business.id == business_id, Business.deleted_at.is_(None)).scalar()
    client_exists = db.query(Client.id).filter(
        Client.id == client_id, Client.deleted_at.is_(None)).scalar()
    if (visit is None or visit.business_id != business_id or visit.client_id != client_id
            or business_exists is None or client_exists is None):
        raise InvalidManageClaim('Guest proof is not available')


def _require_source(source, digest, business_id, visit_id, now=None):
    current = utcnow() if now is None else now
    if (source is None or source.token_hash != digest or source.business_id != business_id
            or source.visit_id != visit_id or source.revoked_at is not None
            or source.expires_at <= current):
        raise InvalidManageClaim('Guest proof is not available')


def _active_targets(db, business_id, client_id, visit_id):
    return db.query(VisitorGrant).filter(
        VisitorGrant.business_id == business_id, VisitorGrant.revoked_at.is_(None),
        or_(and_(VisitorGrant.scope == 'client', VisitorGrant.client_id == client_id),
            and_(VisitorGrant.scope == 'visit', VisitorGrant.visit_id == visit_id)),
    ).order_by(VisitorGrant.id).populate_existing().with_for_update().all()


def consume_guest_manage(db: Session, raw_visitor_session: str | None,
                         raw_manage_token: str | None) -> CompletedManageClaim:
    """Flush one grant and a spent receipt; caller commits or whole-rolls back.

    Account -> Visit -> Client -> source -> Claim -> Grant is shared with future
    client invitations. A consumed receipt never switches to a replacement grant.
    It may be replayed by its owner after legacy token cleanup, while the original
    grant and its fixed visit/client/business still exist. Every error needs rollback.
    """
    digest = _digest(raw_manage_token)
    with db.no_autoflush:
        current = visitor_sessions.get_valid_session(db, raw_visitor_session)
        if current is None:
            raise InvalidVisitorSession('Visitor session is not available')
        account_id, session_id = current.account_id, current.id
        receipt_hint = db.query(VisitorClaim.business_id, VisitorClaim.client_id,
                                VisitorClaim.visit_id, VisitorClaim.kind).filter(
            VisitorClaim.proof_hash == digest).one_or_none()
        source_hint = db.query(BookingManageToken.id, BookingManageToken.business_id,
                               BookingManageToken.visit_id).filter(
            BookingManageToken.token_hash == digest).one_or_none()
        if receipt_hint is not None:
            if receipt_hint.kind != 'manage_visit':
                raise InvalidManageClaim('Guest proof is not available')
            business_id, client_id, visit_id = (receipt_hint.business_id,
                                               receipt_hint.client_id, receipt_hint.visit_id)
        elif source_hint is not None:
            hint = db.query(Visit.client_id).filter(Visit.id == source_hint.visit_id).one_or_none()
            if hint is None:
                raise InvalidManageClaim('Guest proof is not available')
            business_id, client_id, visit_id = source_hint.business_id, hint.client_id, source_hint.visit_id
        else:
            raise InvalidManageClaim('Guest proof is not available')
        try:
            visitor_sessions.lock_verified_account(db, account_id)
        except visitor_sessions.VisitorAccountUnavailable:
            raise InvalidVisitorSession('Visitor session is not available') from None
        _require_session(db, raw_visitor_session, account_id, session_id)
        visit = db.query(Visit.id, Visit.business_id, Visit.client_id).filter(
            Visit.id == visit_id, Visit.deleted_at.is_(None)).with_for_update(key_share=True).one_or_none()
        if visit is None or visit.business_id != business_id or visit.client_id != client_id:
            raise InvalidManageClaim('Guest proof is not available')
        client = db.query(Client.id).filter(Client.id == client_id, Client.deleted_at.is_(None)
                                            ).with_for_update(key_share=True).scalar()
        if client is None:
            raise InvalidManageClaim('Guest proof is not available')
        source = None
        if source_hint is not None:
            source = db.query(BookingManageToken.id, BookingManageToken.token_hash,
                              BookingManageToken.business_id, BookingManageToken.visit_id,
                              BookingManageToken.expires_at, BookingManageToken.revoked_at,
                              BookingManageToken.created_at).filter(
                BookingManageToken.id == source_hint.id).with_for_update().one_or_none()
            # Even an idempotent receipt cannot silently follow a retargeted live row.
            if source is not None and (source.token_hash != digest or source.business_id != business_id
                                       or source.visit_id != visit_id):
                raise InvalidManageClaim('Guest proof is not available')
        receipt = db.query(VisitorClaim).filter(VisitorClaim.proof_hash == digest
                                               ).populate_existing().with_for_update().one_or_none()
        _target(db, visit_id, business_id, client_id)
        _require_session(db, raw_visitor_session, account_id, session_id)
        if receipt is not None:
            if (receipt.kind != 'manage_visit' or receipt.business_id != business_id
                    or receipt.client_id != client_id or receipt.visit_id != visit_id
                    or receipt.revoked_at is not None or receipt.consumed_at is None):
                raise InvalidManageClaim('Guest proof is not available')
            if receipt.consumed_account_id is not None and receipt.consumed_account_id != account_id:
                raise ManageClaimConflict('Guest proof is already claimed')
            if receipt.consumed_account_id is None or receipt.grant_id is None:
                raise InvalidManageClaim('Guest proof is not available')
        else:
            _require_source(source, digest, business_id, visit_id)
        grants = _active_targets(db, business_id, client_id, visit_id)
        if any(grant.account_id != account_id for grant in grants):
            raise ManageClaimConflict('Guest target is already claimed')
        if receipt is not None:
            grant = next((grant for grant in grants if grant.id == receipt.grant_id), None)
            if grant is None or grant.scope != 'visit' or grant.client_id != client_id:
                raise InvalidManageClaim('Guest proof is not available')
            _target(db, visit_id, business_id, client_id)
            _require_session(db, raw_visitor_session, account_id, session_id)
            return CompletedManageClaim(grant.id, account_id, business_id, client_id, visit_id, False)
        grant = next((grant for grant in grants if grant.scope == 'visit'), None)
        if grant is not None and grant.client_id != client_id:
            raise InvalidManageClaim('Guest proof is not available')
        created = grant is None
        if created:
            grant = VisitorGrant(account_id=account_id, business_id=business_id,
                                 client_id=client_id, visit_id=visit_id, scope='visit', created_at=utcnow())
            db.add(grant)
        try:
            db.flush()
            now = utcnow()
            _require_source(source, digest, business_id, visit_id, now)
            receipt = VisitorClaim(kind='manage_visit', proof_hash=digest, business_id=business_id,
                client_id=client_id, visit_id=visit_id, source_manage_token_id=source.id,
                created_at=now, expires_at=source.expires_at,
                consumed_at=now, consumed_account_id=account_id, grant_id=grant.id)
            db.add(receipt)
            db.flush()
        except IntegrityError as exc:
            name = getattr(getattr(exc.orig, 'diag', None), 'constraint_name', None)
            if name in {'uq_visitor_grant_active_visit', 'uq_visitor_claims_proof_hash'}:
                raise ManageClaimConflict('Guest target is already claimed') from None
            raise
        _require_source(source, digest, business_id, visit_id)
        _target(db, visit_id, business_id, client_id)
        _require_session(db, raw_visitor_session, account_id, session_id)
        return CompletedManageClaim(grant.id, account_id, business_id, client_id, visit_id, created)
