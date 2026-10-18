from typing import Optional
from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session
from app.api import deps
from app.api.core.audit import get_audit_logs
from app.api.core.pagination import MAX_PAGE, PaginationParams, PaginatedResponse
from app.db.models.user import User
from app.schemas.analytics import AuditLogOut
from app.api.middleware.rate_limit import limiter, EXPORT_LIMIT
router = APIRouter()
@router.get("/", response_model=PaginatedResponse[AuditLogOut])
def list_audit_logs(
    entity_type: Optional[str] = Query(None),
    entity_id: Optional[int] = Query(None),
    user_id: Optional[int] = Query(None),
    action: Optional[str] = Query(None),
    page: int = Query(1, ge=1, le=MAX_PAGE),
    per_page: int = Query(20, ge=1, le=100),
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.role_required("admin")),
    locale: str = Depends(deps.get_locale),
):
    pagination = PaginationParams(page=page, per_page=per_page)
    logs = get_audit_logs(
        db,
        entity_type=entity_type,
        entity_id=entity_id,
        user_id=user_id,
        action=action,
        limit=pagination.limit,
        offset=pagination.offset,
    )
    from app.db.models.audit_log import AuditLog
    q = db.query(AuditLog)
    if entity_type:
        q = q.filter(AuditLog.entity_type == entity_type)
    if entity_id:
        q = q.filter(AuditLog.entity_id == entity_id)
    if user_id:
        q = q.filter(AuditLog.user_id == user_id)
    if action:
        q = q.filter(AuditLog.action == action)
    total = q.count()
    return PaginatedResponse.create(
        items=[AuditLogOut.model_validate(log) for log in logs],
        total=total,
        page=page,
        per_page=per_page,
    )
@router.get("/export/csv")
@limiter.limit(EXPORT_LIMIT)
def export_audit_logs_csv(
    request: Request,
    entity_type: Optional[str] = Query(None),
    entity_id: Optional[int] = Query(None),
    user_id: Optional[int] = Query(None),
    action: Optional[str] = Query(None),
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.role_required("admin")),
):
    from app.api.core.export import export_to_csv
    logs = get_audit_logs(
        db,
        entity_type=entity_type,
        entity_id=entity_id,
        user_id=user_id,
        action=action,
        limit=10000,  
        offset=0,
    )
    export_data = [
        {
            'ID': log.id,
            'Entity Type': log.entity_type,
            'Entity ID': log.entity_id,
            'Action': log.action,
            'User ID': log.user_id,
            'User Name': log.user_name,
            'Description': log.description,
            'IP Address': log.ip_address,
            'Created At': log.created_at.isoformat(),
        }
        for log in logs
    ]
    return export_to_csv(export_data, filename="audit_logs_export.csv")
