"""Lock one explicitly granted visitor target; the caller owns the transaction.

This resolver has no booking, billing or cancellation policy. Every exception requires
whole caller rollback. Facts and their locks belong to the same open transaction;
revalidate after the command's last wait/flush, before committing anything.
"""
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import and_, inspect, or_, select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.api.core import visitor_history, visitor_sessions
from app.api.core.clock import utcnow
from app.db.models.business import Business
from app.db.models.client import Client
from app.db.models.visit import Visit
from app.db.models.visitor_access import VisitorGrant
from app.db.models.visitor_account import VisitorAccount
from app.db.models.visitor_auth import VisitorSession


class InvalidActionSession(ValueError):
    """The exact visitor credential is no longer usable."""


class ActionTargetUnavailable(ValueError):
    """Unknown, foreign and no-longer-granted targets share this error."""


class ActionBusy(ValueError):
    """Retry only after the caller rolls back the whole transaction."""


class DirtyActionState(ValueError):
    """Pending authority/target edits cannot be refreshed away or silently flushed."""


@dataclass(frozen=True)
class LockedActionTarget:
    account_id: int
    session_id: int
    visit_id: int
    business_id: int
    client_id: int
    specialist_id: int
    grant_id: int
    grant_scope: str
    status: str | None
    date_start: datetime
    client_confirmed_at: datetime | None
    checked_at: datetime
    _transaction: object = field(repr=False, compare=False)


def _unavailable():
    raise ActionTargetUnavailable("Visitor action target is not available")


def _reject_pending(db, targets=()):
    for row in db.new.union(db.dirty).union(db.deleted):
        state = inspect(row)
        authority = isinstance(row, (VisitorAccount, VisitorSession, VisitorGrant))
        matching = any(isinstance(row, model) and (state.identity == (row_id,)
                       or state.dict.get("id") == row_id) for model, row_id in targets)
        if authority or matching:
            raise DirtyActionState("Pending visitor authority or target changes are not supported")


def _session(db, raw, account_id=None, session_id=None):
    current = visitor_sessions.get_valid_session(db, raw)
    if (current is None or (account_id is not None and current.account_id != account_id)
            or (session_id is not None and current.id != session_id)):
        raise InvalidActionSession("Visitor session is not available")
    now = utcnow()
    if current.expires_at <= now:
        raise InvalidActionSession("Visitor session is not available")
    return current, now


def _grants(account_id, business_id, client_id, visit_id):
    return select(VisitorGrant.id, VisitorGrant.scope).where(
        VisitorGrant.account_id == account_id, VisitorGrant.business_id == business_id,
        VisitorGrant.client_id == client_id, VisitorGrant.revoked_at.is_(None),
        or_(and_(VisitorGrant.scope == "client", VisitorGrant.visit_id.is_(None)),
            and_(VisitorGrant.scope == "visit", VisitorGrant.visit_id == visit_id)))


def _fresh_facts(db, raw, *, account_id, session_id, visit_id, business_id,
                 client_id, grant_id, grant_scope, transaction):
    row = db.execute(visitor_history.visit_detail_query(account_id, visit_id).add_columns(
        Visit.client_id, Visit.specialist_id, Visit.client_confirmed_at).where(
            Visit.business_id == business_id, Visit.client_id == client_id)).one_or_none()
    grant = db.execute(_grants(account_id, business_id, client_id, visit_id).where(
        VisitorGrant.id == grant_id, VisitorGrant.scope == grant_scope)).one_or_none()
    if row is None or grant is None:
        _unavailable()
    _, now = _session(db, raw, account_id, session_id)
    return LockedActionTarget(account_id, session_id, visit_id, business_id, client_id,
        row.specialist_id, grant_id, grant_scope, row.status, row.date_start,
        row.client_confirmed_at, now, transaction)


def _busy_or_raise(error):
    code = getattr(error.orig, "pgcode", None) or getattr(error.orig, "sqlstate", None)
    if code in {"55P03", "40P01"}:
        raise ActionBusy("Visitor action target is busy; retry after rollback") from None
    raise error


def resolve_action_target(db: Session, raw_session: str | None, visit_id: int) -> LockedActionTarget:
    """Account -> Business SHARE -> Visit/Client/Grant NOWAIT, without any writes."""
    try:
        with db.no_autoflush:
            _reject_pending(db, ((Visit, visit_id),))
            current, _ = _session(db, raw_session)
            account_id, session_id = current.account_id, current.id
            try:
                visitor_sessions.lock_verified_account(db, account_id)
            except visitor_sessions.VisitorAccountUnavailable:
                raise InvalidActionSession("Visitor session is not available") from None
            _session(db, raw_session, account_id, session_id)
            if isinstance(visit_id, bool) or not isinstance(visit_id, int) or visit_id <= 0:
                _unavailable()
            hint = db.execute(visitor_history.visit_detail_query(account_id, visit_id).add_columns(
                Visit.client_id)).one_or_none()
            if hint is None:
                _unavailable()
            business_id, client_id = hint.business_id, hint.client_id
            _reject_pending(db, ((Visit, visit_id), (Business, business_id), (Client, client_id)))
            business = db.execute(select(Business.id).where(Business.id == business_id,
                Business.deleted_at.is_(None)).with_for_update(read=True)).scalar_one_or_none()
            _session(db, raw_session, account_id, session_id)
            if business is None:
                _unavailable()
            visit = db.execute(select(Visit.id).where(Visit.id == visit_id,
                Visit.business_id == business_id, Visit.client_id == client_id,
                Visit.deleted_at.is_(None)).with_for_update(nowait=True)).scalar_one_or_none()
            if visit is None:
                _unavailable()
            client = db.execute(select(Client.id).where(Client.id == client_id,
                Client.deleted_at.is_(None)).with_for_update(read=True, nowait=True)).scalar_one_or_none()
            if client is None:
                _unavailable()
            grant = db.execute(_grants(account_id, business_id, client_id, visit_id).order_by(
                VisitorGrant.id).limit(1).with_for_update(read=True, nowait=True)).one_or_none()
            if grant is None:
                _unavailable()
            return _fresh_facts(db, raw_session, account_id=account_id, session_id=session_id,
                visit_id=visit_id, business_id=business_id, client_id=client_id,
                grant_id=grant.id, grant_scope=grant.scope, transaction=db.get_nested_transaction() or db.get_transaction())
    except DBAPIError as error:
        _busy_or_raise(error)


def revalidate_action_target(db: Session, raw_session: str | None,
                             target: LockedActionTarget) -> LockedActionTarget:
    """Recheck the exact actor and scope while the caller still holds these locks."""
    if (not isinstance(target, LockedActionTarget) or target._transaction is None
            or (db.get_nested_transaction() or db.get_transaction()) is not target._transaction
            or not target._transaction.is_active):
        raise DirtyActionState("Visitor action facts require their original open transaction")
    try:
        with db.no_autoflush:
            _reject_pending(db, ((Visit, target.visit_id), (Business, target.business_id),
                                 (Client, target.client_id)))
            _session(db, raw_session, target.account_id, target.session_id)
            return _fresh_facts(db, raw_session, account_id=target.account_id,
                session_id=target.session_id, visit_id=target.visit_id,
                business_id=target.business_id, client_id=target.client_id,
                grant_id=target.grant_id, grant_scope=target.grant_scope,
                transaction=target._transaction)
    except DBAPIError as error:
        _busy_or_raise(error)
