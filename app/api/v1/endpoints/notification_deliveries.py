"""Owner-facing delivery log for one business.

Only delivery metadata is returned: the outbox payload (which can name a client and visit)
and any management tokens stay out of this view. Recipients are masked.
"""
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import desc
from sqlalchemy.orm import Session

from app.api import deps
from app.api.core import notification_preferences, notification_worker
from app.api.core.i18n import _
from app.api.core.notification_access import notification_business_id
from app.api.core.pagination import PaginatedResponse, PaginationParams, MAX_PAGE
from app.db.models.notification_outbox import NotificationOutbox
from app.db.models.user import User
from app.schemas.notification_delivery import NotificationDeliveryOut

router = APIRouter()

DeliveryStatus = Literal["pending", "processing", "sent", "captured", "skipped", "failed", "cancelled"]
RETRYABLE_STATUSES = ("failed", "skipped")


def _mask(value: str) -> str:
    local, at, domain = value.partition("@")
    if not at:
        return f"{value[:1]}***" if value else "***"
    return f"{local[:1]}***@{domain}"


def _out(row: NotificationOutbox) -> NotificationDeliveryOut:
    return NotificationDeliveryOut(
        id=row.id,
        visit_id=row.visit_id,
        kind=row.kind,
        channel=row.channel,
        recipient=_mask(row.recipient),
        status=row.status,
        attempts=row.attempts or 0,
        provider=row.provider,
        scheduled_at=row.scheduled_at,
        sent_at=row.sent_at,
        last_error=row.last_error,
        retryable=row.status in RETRYABLE_STATUSES and not row.provider_message_id,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


@router.get("", response_model=PaginatedResponse[NotificationDeliveryOut], summary="List notification deliveries")
def list_notification_deliveries(
    status: Optional[DeliveryStatus] = Query(None),
    kind: Optional[str] = Query(None, max_length=50),
    page: int = Query(1, ge=1, le=MAX_PAGE),
    per_page: int = Query(20, ge=1, le=100),
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
    locale: str = Depends(deps.get_locale),
):
    business_id = notification_business_id(db, current_user, locale)
    pagination = PaginationParams(page=page, per_page=per_page)
    query = db.query(NotificationOutbox).filter(NotificationOutbox.business_id == business_id)
    if status:
        query = query.filter(NotificationOutbox.status == status)
    if kind:
        query = query.filter(NotificationOutbox.kind == kind)
    total = query.count()
    rows = (
        query.order_by(desc(NotificationOutbox.created_at), desc(NotificationOutbox.id))
        .offset(pagination.offset)
        .limit(pagination.limit)
        .all()
    )
    return PaginatedResponse.create(
        items=[_out(row) for row in rows],
        total=total,
        page=pagination.page,
        per_page=pagination.per_page,
    )


@router.post("/{message_id}/retry", response_model=NotificationDeliveryOut, summary="Retry a failed delivery")
def retry_notification_delivery(
    message_id: int,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
    locale: str = Depends(deps.get_locale),
):
    business_id = notification_business_id(db, current_user, locale)
    row = (
        db.query(NotificationOutbox)
        .filter(
            NotificationOutbox.id == message_id,
            NotificationOutbox.business_id == business_id,
        )
        .first()
    )
    if row is None:
        raise HTTPException(status_code=404, detail=_("notification_delivery_not_found", locale))
    if row.status not in RETRYABLE_STATUSES or row.provider_message_id:
        raise HTTPException(status_code=409, detail=_("notification_not_retryable", locale))
    channel = notification_preferences.enabled_channel(db, business_id)
    if channel is None or row.channel != channel:
        raise HTTPException(status_code=409, detail=_("notification_delivery_disabled", locale))
    if not notification_worker.requeue_failed(db, message_id, business_id):
        raise HTTPException(status_code=409, detail=_("notification_not_retryable", locale))
    db.refresh(row)
    return _out(row)
