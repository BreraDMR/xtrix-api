"""Owner-facing journal. It only ever reads the business-scoped table.

The legacy ``/audit`` router is a platform-wide admin log and is intentionally not
reachable from here, even for a business admin.
"""
import json
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from app.api import deps
from app.api.core import audit_service
from app.api.core.i18n import _
from app.api.core.pagination import MAX_PAGE, PaginatedResponse, PaginationParams
from app.api.core.permissions import (
    can_view_financials,
    get_staff_record,
    get_user_business_id,
)
from app.api.middleware.rate_limit import EXPORT_LIMIT, limiter
from app.db.models.user import User
from app.schemas.business_audit import BusinessAuditEventOut
from app.schemas.datetimes import UtcDatetime

router = APIRouter()


def _scope(db: Session, user: User, locale: str) -> audit_service.AuditScope:
    if user.role == "admin":
        # Platform admins keep their separate /audit log; in a salon they see only
        # that salon, and an admin without a business has no tenant journal at all.
        if user.business_id:
            return audit_service.AuditScope(business_id=user.business_id)
        raise HTTPException(status_code=403, detail=_("not_allowed", locale))

    if user.role in ("business_owner", "specialist") and not get_staff_record(db, user):
        business_id = get_user_business_id(user, db)
        if business_id:
            return audit_service.AuditScope(business_id=business_id)
        # A solo specialist (or an owner who has not created a business) is the
        # owner of their own journal.
        return audit_service.AuditScope(owner_user_id=user.id)

    raise HTTPException(status_code=403, detail=_("not_allowed", locale))


def _hidden_action_prefixes(db: Session, user: User) -> tuple[str, ...]:
    """Event families this caller is not allowed to see, even inside their own tenant."""
    hidden = []
    if not can_view_financials(user, db):
        hidden.append("payment.")
    can_read_questionnaires = user.role in ("admin", "business_owner") or (
        user.role == "specialist" and not get_staff_record(db, user)
    )
    if not can_read_questionnaires:
        hidden.append("questionnaire.")
    return tuple(hidden)


def _filters(
    scope: audit_service.AuditScope,
    db: Session,
    *,
    action: Optional[str],
    entity_type: Optional[str],
    actor_user_id: Optional[int],
    date_from: Optional[datetime],
    date_to: Optional[datetime],
    exclude_action_prefixes: tuple[str, ...] = (),
    limit: int,
    offset: int = 0,
):
    return audit_service.list_events(
        db,
        scope,
        action=action,
        entity_type=entity_type,
        actor_user_id=actor_user_id,
        date_from=date_from,
        date_to=date_to,
        exclude_action_prefixes=exclude_action_prefixes,
        limit=limit,
        offset=offset,
    )


@router.get("", response_model=PaginatedResponse[BusinessAuditEventOut], summary="Read this business's action journal")
def list_business_audit_events(
    action: Optional[str] = Query(None, max_length=64),
    entity_type: Optional[str] = Query(None, max_length=32),
    actor_user_id: Optional[int] = Query(None, gt=0),
    date_from: Optional[UtcDatetime] = None,
    date_to: Optional[UtcDatetime] = None,
    page: int = Query(1, ge=1, le=MAX_PAGE),
    per_page: int = Query(20, ge=1, le=100),
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
    locale: str = Depends(deps.get_locale),
):
    scope = _scope(db, current_user, locale)
    pagination = PaginationParams(page=page, per_page=per_page)
    rows, total = _filters(
        scope,
        db,
        action=action,
        entity_type=entity_type,
        actor_user_id=actor_user_id,
        date_from=date_from,
        date_to=date_to,
        exclude_action_prefixes=_hidden_action_prefixes(db, current_user),
        limit=pagination.limit,
        offset=pagination.offset,
    )
    return PaginatedResponse.create(
        items=[BusinessAuditEventOut.model_validate(row) for row in rows],
        total=total,
        page=page,
        per_page=per_page,
    )


@router.get("/export/csv", summary="Export this business's action journal")
@limiter.limit(EXPORT_LIMIT)
def export_business_audit_csv(
    request: Request,
    action: Optional[str] = Query(None, max_length=64),
    entity_type: Optional[str] = Query(None, max_length=32),
    actor_user_id: Optional[int] = Query(None, gt=0),
    date_from: Optional[UtcDatetime] = None,
    date_to: Optional[UtcDatetime] = None,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
    locale: str = Depends(deps.get_locale),
):
    from app.api.core.export import export_to_csv

    scope = _scope(db, current_user, locale)
    rows, _total = _filters(
        scope,
        db,
        action=action,
        entity_type=entity_type,
        actor_user_id=actor_user_id,
        date_from=date_from,
        date_to=date_to,
        exclude_action_prefixes=_hidden_action_prefixes(db, current_user),
        limit=10_000,
    )
    export_data = [
        {
            "ID": row.id,
            "When": row.created_at.isoformat(),
            "Actor": row.actor_name or "",
            "Role": row.actor_role or "",
            "Action": row.action,
            "Entity": row.entity_type,
            "Entity ID": row.entity_id,
            "Request ID": row.request_id or "",
            "Changes": json.dumps(row.changes, ensure_ascii=False, sort_keys=True)
            if row.changes
            else "",
        }
        for row in rows
    ]
    return export_to_csv(export_data, filename="business_audit_export.csv")
