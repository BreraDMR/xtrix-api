"""Acquire scheduling anchors before the ordinary visitor mutation target.

Every refusal requires whole caller rollback. No booking fields are changed here;
these facts are usable only in their original transaction, never as a slot token.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.api.core import crm_metering, visitor_action_target as targets, visitor_history, visitor_sessions
from app.api.core.visit_chain import ChainLine
from app.db.models.business import Business
from app.db.models.user import User
from app.db.models.visit import Visit
from app.db.models.visit_item import VisitItem


class RescheduleUnavailable(ValueError):
    """The stored chain cannot safely be moved through public booking."""


@dataclass(frozen=True)
class LockedRescheduleTarget:
    authority: targets.LockedActionTarget
    lines: tuple[ChainLine, ...]
    signature: tuple


_PARENT = ('id', 'business_id', 'client_id', 'specialist_id', 'service_id', 'service_name',
           'status', 'date_start', 'date_end', 'price', 'currency_snapshot')
_ITEM = ('id', 'position', 'service_id', 'specialist_id', 'service_name', 'price',
         'duration_minutes', 'buffer_before_minutes', 'buffer_after_minutes', 'date_start', 'date_end')


def _snapshot(db, account_id, visit_id):
    scoped = visitor_history.visit_detail_query(account_id, visit_id).with_only_columns(
        *(getattr(Visit, name).label(name) for name in _PARENT)).subquery()
    rows = db.execute(select(*(scoped.c[name] for name in _PARENT),
        *(getattr(VisitItem, name).label('item_' + name) for name in _ITEM)
        ).select_from(scoped).outerjoin(VisitItem, VisitItem.visit_id == scoped.c.id)
        .order_by(VisitItem.position, VisitItem.id)).mappings().all()
    if not rows:
        raise targets.ActionTargetUnavailable('Visitor action target is not available')
    parent = tuple(rows[0][name] for name in _PARENT)
    items = tuple(tuple(row['item_' + name] for name in _ITEM)
                  for row in rows if row['item_id'] is not None)
    return parent, items


def _lines(signature):
    parent, items = signature
    p = dict(zip(_PARENT, parent))
    if not items:
        # An unknown legacy duration is not evidence of today's catalog duration.
        if p['date_end'] is None or p['date_end'] <= p['date_start']:
            raise RescheduleUnavailable('Stored booking duration is not available')
        minutes = (p['date_end'] - p['date_start']).total_seconds() / 60
        if minutes != int(minutes):
            raise RescheduleUnavailable('Stored booking duration is not available')
        return (ChainLine(p['service_id'], p['specialist_id'], p['service_name'],
                         Decimal(str(p['price'])) if p['price'] is not None else None, int(minutes)),)
    lines = []
    cursor = p['date_start']
    for position, values in enumerate(items):
        row = dict(zip(_ITEM, values))
        duration = row['duration_minutes']
        if (row['position'] != position or not duration or duration < 1
                or row['buffer_before_minutes'] is None or row['buffer_before_minutes'] < 0
                or row['buffer_after_minutes'] is None or row['buffer_after_minutes'] < 0
                or row['date_start'] != cursor
                or row['date_end'] != cursor + timedelta(minutes=duration)):
            raise RescheduleUnavailable('Stored booking chain is inconsistent')
        lines.append(ChainLine(row['service_id'], row['specialist_id'], row['service_name'],
            row['price'], duration, row['buffer_before_minutes'], row['buffer_after_minutes']))
        cursor = row['date_end']
    if cursor != p['date_end']:
        raise RescheduleUnavailable('Stored booking chain is inconsistent')
    return tuple(lines)


def _clean(db, visit_id):
    targets._reject_pending(db, ((Visit, visit_id),))
    if any(isinstance(row, (VisitItem, User)) for row in db.new.union(db.dirty).union(db.deleted)):
        raise targets.DirtyActionState('Save scheduling edits before moving a booking')


def resolve_reschedule_target(db: Session, raw_session: str | None, visit_id: int,
                              new_start: datetime) -> LockedRescheduleTarget:
    """Account -> quota -> sorted Staff NOWAIT -> try slots -> ordinary target.

    Only exact authorized scalar hints enter quota/staff resolution. After all
    anchors, compare the complete stored chain again; a changed hint is retried
    rather than locking another staff set in a different order.
    """
    try:
        with db.no_autoflush:
            _clean(db, visit_id)
            current, _ = targets._session(db, raw_session)
            account_id, session_id = current.account_id, current.id
            try:
                visitor_sessions.lock_verified_account(db, account_id)
            except visitor_sessions.VisitorAccountUnavailable:
                raise targets.InvalidActionSession('Visitor session is not available') from None
            targets._session(db, raw_session, account_id, session_id)
            if isinstance(visit_id, bool) or not isinstance(visit_id, int) or visit_id <= 0:
                raise targets.ActionTargetUnavailable('Visitor action target is not available')
            signature = _snapshot(db, account_id, visit_id)
            parent = dict(zip(_PARENT, signature[0]))
            lines = _lines(signature)
            masters = {parent['specialist_id'], *(line.specialist_id for line in lines)}
            if any(isinstance(value, bool) or not isinstance(value, int) or value <= 0 for value in masters):
                raise RescheduleUnavailable('Stored booking staff are not available')
            master_ids = sorted(masters)
            try:
                crm_metering.enforce_visit_quota(db, parent['business_id'], parent['specialist_id'],
                    new_start, parent['status'], exclude_id=visit_id)
            except HTTPException as exc:
                if exc.status_code not in (402, 403):
                    raise
                raise RescheduleUnavailable('Booking changes are not available') from None
            targets._session(db, raw_session, account_id, session_id)
            staff = db.execute(select(User.id).where(User.id.in_(master_ids),
                User.business_id == parent['business_id'], User.deleted_at.is_(None),
                User.archived_at.is_(None)).order_by(User.id).with_for_update(nowait=True)).scalars().all()
            if staff != master_ids:
                raise RescheduleUnavailable('Booking staff are not available')
            for master_id in master_ids:
                acquired = db.execute(text('SELECT pg_try_advisory_xact_lock(hashtextextended(:key,0))'),
                    {'key': f'visit-slot:{master_id}'}).scalar_one()
                if not acquired:
                    raise targets.ActionBusy('Booking slot is busy; retry after rollback')
            # Do not wait on a business writer while holding its staff anchors.
            business = db.execute(select(Business.id).where(Business.id == parent['business_id'],
                Business.deleted_at.is_(None)).with_for_update(read=True, nowait=True)).scalar_one_or_none()
            if business is None:
                raise targets.ActionTargetUnavailable('Visitor action target is not available')
            # Freeze the hinted scope before the ordinary resolver can reread it.
            visit = db.execute(select(Visit.id).where(Visit.id == visit_id,
                Visit.business_id == parent['business_id'], Visit.client_id == parent['client_id'],
                Visit.deleted_at.is_(None)).with_for_update(nowait=True)).scalar_one_or_none()
            if visit is None:
                raise targets.ActionTargetUnavailable('Visitor action target is not available')
            authority = targets.resolve_action_target(db, raw_session, visit_id)
            db.execute(select(VisitItem.id).where(VisitItem.visit_id == visit_id).order_by(
                VisitItem.id).with_for_update(nowait=True)).all()
            if _snapshot(db, account_id, visit_id) != signature:
                raise targets.ActionBusy('Booking changed; retry after rollback')
            targets.revalidate_action_target(db, raw_session, authority)
            return LockedRescheduleTarget(authority, lines, signature)
    except DBAPIError as error:
        targets._busy_or_raise(error)


def revalidate_reschedule_target(db: Session, raw_session: str | None,
                                 target: LockedRescheduleTarget) -> targets.LockedActionTarget:
    with db.no_autoflush:
        _clean(db, target.authority.visit_id)
        authority = targets.revalidate_action_target(db, raw_session, target.authority)
        if _snapshot(db, authority.account_id, authority.visit_id) != target.signature:
            raise targets.ActionBusy('Booking changed; retry after rollback')
        return authority
