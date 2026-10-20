from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api import deps
from app.api.core.i18n import _
from app.api.core.permissions import (
    can_view_financials,
    check_client_access,
    get_user_business_id,
)
from app.api.crud import crud_client
from app.api.crud.crud_client_overview import build_client_overview
from app.db.models.user import User
from app.schemas.client_overview import ClientOverviewOut

router = APIRouter()

STAFF_ROLES = ("admin", "business_owner", "specialist", "employee")


@router.get("/{client_id}/overview", response_model=ClientOverviewOut)
def get_client_overview(
    client_id: int,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.role_required(*STAFF_ROLES)),
    locale: str = Depends(deps.get_locale),
):
    client = crud_client.get_client(db, client_id)
    if not client:
        raise HTTPException(status_code=404, detail=_("client_not_found", locale))
    if not check_client_access(db, current_user, client_id):
        raise HTTPException(status_code=403, detail=_("not_allowed", locale))

    business_id = get_user_business_id(current_user, db)
    specialist_id = None
    if business_id is None:
        # a solo specialist keeps working without a business; only their own visits belong here
        if current_user.role != "specialist":
            raise HTTPException(status_code=403, detail=_("business_required", locale))
        specialist_id = current_user.id

    return build_client_overview(
        db,
        client_id=client_id,
        business_id=business_id,
        specialist_id=specialist_id,
        include_financials=can_view_financials(current_user, db),
    )
