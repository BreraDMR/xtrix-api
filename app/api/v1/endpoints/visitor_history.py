"""Read-only visitor history and separate action hints from exact grants."""
from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Path, Query, Request
from sqlalchemy.orm import Session

from app.api import deps, visitor_deps
from app.api.core import visitor_action_facts, visitor_history_views, visitor_sessions
from app.api.core.clock import utcnow
from app.api.core.i18n import _
from app.api.middleware.rate_limit import limiter
from app.api.private_routes import NoStoreRoute
from app.db.models.visitor_auth import VisitorSession
from app.schemas.datetimes import UtcDatetime
from app.schemas.visitor_actions import VisitorBookingAction, VisitorBookingActions
from app.schemas.visitor_history import VisitorHistoryCursor, VisitorHistoryPage, VisitorHistoryVisit

router = APIRouter(route_class=NoStoreRoute)


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
