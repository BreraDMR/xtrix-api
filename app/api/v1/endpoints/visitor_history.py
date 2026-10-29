"""Visitor history, action hints and explicit commands from exact grants."""
from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Path, Query, Request, Response
from sqlalchemy.orm import Session

from app.api import deps, visitor_deps
from app.api.core import booking_manage_policy, visitor_action_facts, visitor_action_target
from app.api.core import visitor_cancel, visitor_confirm, visitor_history_views, visitor_pending_payments, visitor_sessions
from app.api.core import visitor_reschedule, visitor_reschedule_target
from app.api.core.clock import utcnow
from app.api.core.i18n import _
from app.api.core.logger import get_logger
from app.api.middleware.rate_limit import PUBLIC_BOOKING_MANAGE_ACTION_LIMIT, limiter
from app.api.private_routes import NoStoreRoute
from app.db.models.visitor_auth import VisitorSession
from app.db.models.business import Business
from app.schemas.datetimes import UtcDatetime
from app.schemas.public_booking import PublicBookingRescheduleIn
from app.schemas.visitor_actions import VisitorBookingAction, VisitorBookingActions
from app.schemas.visitor_history import VisitorHistoryCursor, VisitorHistoryPage, VisitorHistoryVisit

router = APIRouter(route_class=NoStoreRoute)
logger = get_logger('visitor-actions')


def _require_same_session(db: Session, raw_token: str | None, actor: tuple[int, int], locale: str) -> None:
    # Capture IDs before the projection: populate_existing may refresh the original ORM row.
    with db.no_autoflush:
        current = visitor_sessions.get_valid_session(db, raw_token)
    if current is None or (current.account_id, current.id) != actor:
        raise HTTPException(status_code=401, detail=_("visitor_session_invalid", locale))


@router.get("/visits", response_model=VisitorHistoryPage)
@limiter.limit("60/minute")
def list_visits(request: Request, limit: int = Query(default=25, ge=1, le=100),
                business_id: int | None = Query(default=None, gt=0),
                category: Literal["all", "upcoming", "past", "cancelled"] = Query(default="all"),
                as_of: UtcDatetime | None = Query(default=None),
                after_start: UtcDatetime | None = Query(default=None),
                after_id: int | None = Query(default=None, gt=0),
                session: VisitorSession = Depends(visitor_deps.get_visitor_session),
                x_visitor_token: str | None = Header(default=None, alias="X-Visitor-Token"),
                db: Session = Depends(deps.get_db), locale: str = Depends(deps.get_locale)) -> VisitorHistoryPage:
    if ((after_start is None) != (after_id is None)
            or (after_start is not None and category != "all" and as_of is None)):
        raise HTTPException(status_code=422, detail=_("visitor_history_cursor_invalid", locale))
    actor = (session.account_id, session.id)
    cursor = VisitorHistoryCursor(date_start=after_start, id=after_id, as_of=as_of) if after_start is not None else None
    result = visitor_history_views.list_visits(db, actor[0], limit=limit, cursor=cursor,
                                              business_id=business_id, category=category, as_of=as_of)
    _require_same_session(db, x_visitor_token, actor, locale)
    return result


@router.get("/visits/{visit_id}", response_model=VisitorHistoryVisit)
@limiter.limit("60/minute")
def read_visit(request: Request, visit_id: int = Path(gt=0),
               session: VisitorSession = Depends(visitor_deps.get_visitor_session),
               x_visitor_token: str | None = Header(default=None, alias="X-Visitor-Token"),
               db: Session = Depends(deps.get_db), locale: str = Depends(deps.get_locale)) -> VisitorHistoryVisit:
    actor = (session.account_id, session.id)
    result = visitor_history_views.get_visit(db, actor[0], visit_id)
    _require_same_session(db, x_visitor_token, actor, locale)
    if result is None:
        raise HTTPException(status_code=404, detail=_("visitor_history_visit_unavailable", locale))
    return result


def _get_action_session(
    x_visitor_token: str | None = Header(default=None, alias="X-Visitor-Token"),
    db: Session = Depends(deps.get_db), locale: str = Depends(deps.get_locale),
) -> VisitorSession:
    # Reuse strict visitor authentication without flushing unrelated caller edits.
    with db.no_autoflush:
        return visitor_deps.get_visitor_session(x_visitor_token, db, locale)


@router.get("/visits/{visit_id}/actions", response_model=VisitorBookingActions,
            description="Current action hints. Commands recheck authority, time, billing and catalog; no slot is reserved.")
@limiter.limit("60/minute")
def read_visit_actions(request: Request, visit_id: int = Path(gt=0),
                       session: VisitorSession = Depends(_get_action_session),
                       x_visitor_token: str | None = Header(default=None, alias="X-Visitor-Token"),
                       db: Session = Depends(deps.get_db), locale: str = Depends(deps.get_locale)) -> VisitorBookingActions:
    actor = (session.account_id, session.id)
    result = visitor_action_facts.get_visit_actions(db, actor[0], visit_id, now=utcnow())
    _require_same_session(db, x_visitor_token, actor, locale)
    if result is None:
        raise HTTPException(status_code=404, detail=_("visitor_history_visit_unavailable", locale))
    return VisitorBookingActions(**{
        name: VisitorBookingAction(allowed=decision.allowed, reason=decision.reason)
        for name, decision in (("confirm", result.confirm), ("cancel", result.cancel), ("reschedule", result.reschedule))
    })


@router.post("/visits/{visit_id}/confirm", status_code=204,
             summary="Confirm an explicitly granted visitor booking")
@limiter.limit(PUBLIC_BOOKING_MANAGE_ACTION_LIMIT)
def confirm_visit(request: Request, visit_id: int = Path(gt=0),
                  x_visitor_token: str | None = Header(default=None, alias="X-Visitor-Token"),
                  db: Session = Depends(deps.get_db), locale: str = Depends(deps.get_locale)) -> Response:
    _commit_action(visitor_confirm.confirm_visit, db, x_visitor_token, visit_id, locale)
    return Response(status_code=204)


def _commit_action(command, db, raw_token, visit_id, locale, **kwargs):
    try:
        # The command authenticates the opaque token and rechecks the locked grant.
        result = command(db, raw_token, visit_id, **kwargs)
        db.commit()
    except visitor_action_target.InvalidActionSession:
        db.rollback()
        raise HTTPException(401, detail=_("visitor_session_invalid", locale)) from None
    except visitor_action_target.ActionTargetUnavailable:
        db.rollback()
        raise HTTPException(404, detail=_("visitor_history_visit_unavailable", locale)) from None
    except visitor_action_target.ActionBusy:
        db.rollback()
        raise HTTPException(409, detail={"code": "visitor_action_retry",
                                        "message": _("visitor_action_retry", locale)}) from None
    except visitor_reschedule_target.RescheduleUnavailable:
        db.rollback()
        raise HTTPException(409, detail={"code": "booking_reschedule_unavailable",
                                        "message": _("booking_reschedule_unavailable", locale)}) from None
    except visitor_pending_payments.PendingPaymentConflict:
        db.rollback()
        raise HTTPException(409, detail={"code": "booking_cancel_unavailable",
                                        "message": _("booking_cancel_unavailable", locale)}) from None
    except booking_manage_policy.BookingActionNotAllowed as exc:
        db.rollback()
        raise HTTPException(409, detail={"code": exc.reason, "message": _(exc.reason, locale)}) from None
    except Exception:
        db.rollback()
        raise
    return result


@router.post("/visits/{visit_id}/cancel", status_code=204,
             summary="Cancel an explicitly granted visitor booking")
@limiter.limit(PUBLIC_BOOKING_MANAGE_ACTION_LIMIT)
def cancel_visit(request: Request, visit_id: int = Path(gt=0),
                 x_visitor_token: str | None = Header(default=None, alias="X-Visitor-Token"),
                 db: Session = Depends(deps.get_db), locale: str = Depends(deps.get_locale)) -> Response:
    result = _commit_action(visitor_cancel.cancel_visit, db, x_visitor_token, visit_id, locale)
    _after_released_slot(db, result)
    return Response(status_code=204)


@router.post('/visits/{visit_id}/reschedule', status_code=204,
             summary='Move an explicitly granted visitor booking without changing its stored quote')
@limiter.limit(PUBLIC_BOOKING_MANAGE_ACTION_LIMIT)
def reschedule_visit(request: Request, payload: PublicBookingRescheduleIn, visit_id: int = Path(gt=0),
                     x_visitor_token: str | None = Header(default=None, alias='X-Visitor-Token'),
                     db: Session = Depends(deps.get_db), locale: str = Depends(deps.get_locale)) -> Response:
    result = _commit_action(visitor_reschedule.reschedule_visit, db, x_visitor_token,
                            visit_id, locale, new_start=payload.start)
    _after_released_slot(db, result)
    return Response(status_code=204)


def _after_released_slot(db, result):
    if result.changed:
        try:
            from app.api.core import waitlist_service

            business = db.get(Business, result.business_id)
            if business is not None and business.deleted_at is None:
                waitlist_service.release_slot_quietly(db, business, result.service_id,
                                                     result.specialist_id, result.start)
        except Exception as exc:
            db.rollback()
            logger.warning('Post-booking-action slot matching failed for visit_id=%s: %s',
                           result.id, type(exc).__name__)
        try:
            from app.api.core.redis_client import invalidate_visit

            invalidate_visit(result.id)
        except Exception as exc:
            logger.warning('Post-booking-action cache refresh failed for visit_id=%s: %s',
                           result.id, type(exc).__name__)
