from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from app.api import deps
from app.api.core.i18n import _
from app.api.core.archiving import (
    archive_old_clients,
    archive_old_visits,
    archive_old_services,
    unarchive,
    get_archived_count,
)
from app.db.models.user import User
from app.db.models.client import Client
from app.db.models.visit import Visit
from app.db.models.service import Service
router = APIRouter()
@router.post("/clients/archive")
def archive_clients(
    days_old: int = Query(365, ge=30, le=3650, description="Archive clients older than N days"),
    owner_id: Optional[int] = Query(None, description="Filter by owner ID"),
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.role_required("admin")),
    locale: str = Depends(deps.get_locale),
):
    if current_user.role != "admin" and owner_id != current_user.id:
        raise HTTPException(status_code=403, detail=_("not_allowed", locale))
    archived_count = archive_old_clients(db, days_old=days_old, owner_id=owner_id or current_user.id)
    return {
        "message": f"Archived {archived_count} clients",
        "archived_count": archived_count,
    }
@router.post("/visits/archive")
def archive_visits(
    days_old: int = Query(365, ge=30, le=3650, description="Archive visits older than N days"),
    status: Optional[str] = Query("completed", description="Only archive visits with this status"),
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.role_required("admin")),
    locale: str = Depends(deps.get_locale),
):
    archived_count = archive_old_visits(db, days_old=days_old, status=status)
    return {
        "message": f"Archived {archived_count} visits",
        "archived_count": archived_count,
    }
@router.post("/services/archive")
def archive_services(
    days_old: int = Query(180, ge=30, le=3650, description="Archive services older than N days"),
    inactive_only: bool = Query(True, description="Only archive inactive services"),
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.role_required("admin")),
    locale: str = Depends(deps.get_locale),
):
    archived_count = archive_old_services(db, days_old=days_old, inactive_only=inactive_only)
    return {
        "message": f"Archived {archived_count} services",
        "archived_count": archived_count,
    }
@router.post("/clients/{client_id}/unarchive")
def unarchive_client(
    client_id: int,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.role_required("admin")),
    locale: str = Depends(deps.get_locale),
):
    success = unarchive(db, Client, client_id)
    if not success:
        raise HTTPException(status_code=404, detail=_("client_not_found", locale))
    return {"message": "Client unarchived successfully"}
@router.post("/visits/{visit_id}/unarchive")
def unarchive_visit(
    visit_id: int,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.role_required("admin")),
    locale: str = Depends(deps.get_locale),
):
    success = unarchive(db, Visit, visit_id)
    if not success:
        raise HTTPException(status_code=404, detail=_("visit_not_found", locale))
    return {"message": "Visit unarchived successfully"}
@router.get("/stats")
def get_archiving_stats(
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.role_required("admin")),
):
    return {
        "archived_clients": get_archived_count(db, Client),
        "archived_visits": get_archived_count(db, Visit),
        "archived_services": get_archived_count(db, Service),
    }
