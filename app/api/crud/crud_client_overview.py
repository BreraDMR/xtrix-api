"""Read model behind the client card: history, upcoming visit and payments.

The whole overview is built from a fixed number of queries, whatever the number
of visits: aggregates for the counters, one page for upcoming visits and one for
history. Prices are only selected when the caller may see financials.
"""
from datetime import datetime
from typing import Optional

from sqlalchemy import case, func, or_
from sqlalchemy.orm import Session, joinedload

from app.api.core.clock import utcnow
from app.api.crud.crud_visit import _no_business_but_ours
from app.db.models.business import Business
from app.db.models.visit import Visit
from app.schemas.client_overview import (
    ClientOverviewOut,
    ClientOverviewPayments,
    ClientOverviewStats,
    ClientOverviewVisit,
)

RECENT_VISIT_LIMIT = 20
UPCOMING_VISIT_LIMIT = 5
UPCOMING_STATUSES = ("planned", "in_progress")


def _visit_query(db: Session, client_id: int, business_id: Optional[int], specialist_id: Optional[int]):
    query = db.query(Visit).filter(
        Visit.client_id == client_id,
        Visit.deleted_at.is_(None),
        Visit.archived_at.is_(None),
    )
    if business_id:
        query = query.filter(or_(Visit.business_id == business_id, _no_business_but_ours(business_id)))
    elif specialist_id:
        query = query.filter(Visit.specialist_id == specialist_id)
    return query


def _visit_out(visit: Visit, include_financials: bool) -> ClientOverviewVisit:
    service_name = visit.service_name or (visit.service.name if visit.service else None)
    return ClientOverviewVisit(
        id=visit.id,
        date_start=visit.date_start,
        date_end=visit.date_end,
        status=visit.status or "planned",
        service_name=service_name,
        specialist_name=visit.specialist.full_name if visit.specialist else None,
        price=visit.price if include_financials else None,
        payment_method=visit.payment_method if include_financials else None,
    )


def build_client_overview(
    db: Session,
    client_id: int,
    business_id: Optional[int],
    specialist_id: Optional[int],
    include_financials: bool,
    now: Optional[datetime] = None,
) -> ClientOverviewOut:
    now = now or utcnow()

    total, completed, cancelled, last_visit = (
        _visit_query(db, client_id, business_id, specialist_id)
        .with_entities(
            func.count(Visit.id),
            func.coalesce(func.sum(case((Visit.status == "completed", 1), else_=0)), 0),
            func.coalesce(func.sum(case((Visit.status == "cancelled", 1), else_=0)), 0),
            func.max(case((Visit.status == "completed", Visit.date_start), else_=None)),
        )
        .one()
    )

    visit_options = (joinedload(Visit.specialist), joinedload(Visit.service))
    upcoming = (
        _visit_query(db, client_id, business_id, specialist_id)
        .options(*visit_options)
        .filter(Visit.status.in_(UPCOMING_STATUSES), Visit.date_start >= now)
        .order_by(Visit.date_start.asc(), Visit.id.asc())
        .limit(UPCOMING_VISIT_LIMIT)
        .all()
    )
    recent = (
        _visit_query(db, client_id, business_id, specialist_id)
        .options(*visit_options)
        .filter(Visit.date_start < now)
        .order_by(Visit.date_start.desc(), Visit.id.desc())
        .limit(RECENT_VISIT_LIMIT)
        .all()
    )

    payments = None
    if include_financials:
        paid_visits, total_paid = (
            _visit_query(db, client_id, business_id, specialist_id)
            .filter(Visit.status == "completed", Visit.price.isnot(None), Visit.price > 0)
            .with_entities(func.count(Visit.id), func.coalesce(func.sum(Visit.price), 0.0))
            .one()
        )
        currency = "EUR"
        if business_id:
            currency = db.query(Business.currency).filter(Business.id == business_id).scalar() or "EUR"
        payments = ClientOverviewPayments(
            currency=currency, total_paid=float(total_paid or 0.0), paid_visits=int(paid_visits or 0)
        )

    return ClientOverviewOut(
        client_id=client_id,
        stats=ClientOverviewStats(
            total_visits=int(total or 0),
            completed_visits=int(completed or 0),
            cancelled_visits=int(cancelled or 0),
            last_visit_at=last_visit,
            next_visit_at=upcoming[0].date_start if upcoming else None,
        ),
        next_visit=_visit_out(upcoming[0], include_financials) if upcoming else None,
        upcoming_visits=[_visit_out(visit, include_financials) for visit in upcoming],
        recent_visits=[_visit_out(visit, include_financials) for visit in recent],
        payments=payments,
    )
