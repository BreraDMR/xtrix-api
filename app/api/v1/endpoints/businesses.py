from datetime import datetime
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session
from app.api import deps
from app.api.core.i18n import _
from app.api.core.pagination import MAX_OFFSET
from app.api.crud import crud_business
from app.db.models.user import User
from app.api.private_routes import NoStoreRoute
from app.schemas.business import BusinessCreate, BusinessOut, BusinessUpdate
from app.schemas.limits import Currency
router = APIRouter()
legacy_router = APIRouter(route_class=NoStoreRoute)
@router.post("/", response_model=BusinessOut, summary="Create business")
def create_business(
    business: BusinessCreate,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
    locale: str = Depends(deps.get_locale),
):
    if current_user.role not in ["admin", "business_owner"]:
        raise HTTPException(status_code=403, detail=_("not_allowed", locale))
    if current_user.business_id:
        raise HTTPException(
            status_code=400,
            detail=_("user_already_has_business", locale)
        )
    new_business = crud_business.create_business(db=db, business_in=business)
    current_user.business_id = new_business.id
    db.add(current_user)
    db.commit()
    db.refresh(current_user)
    return BusinessOut.model_validate(new_business)
@router.get("/", response_model=List[BusinessOut], summary="List businesses")
def list_businesses(
    search: Optional[str] = None,
    skip: int = Query(0, ge=0, le=MAX_OFFSET),
    limit: int = Query(100, ge=1, le=100),
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
    locale: str = Depends(deps.get_locale),
):
    # there is no public catalog (yet) - anonymous callers used to get every business on the
    # platform with phones and e-mails. Admins see all, everyone else just their own.
    from app.db.models.business import Business
    if current_user.role == "admin":
        query = db.query(Business).filter(Business.deleted_at.is_(None))
        if search:
            query = query.filter(Business.name.ilike(f"%{search}%"))
        businesses = query.offset(skip).limit(limit).all()
    elif current_user.business_id:
        business = crud_business.get_business(db=db, business_id=current_user.business_id)
        businesses = [business] if business else []
    else:
        businesses = []
    return [BusinessOut.model_validate(b) for b in businesses]
@router.get("/me", response_model=BusinessOut, summary="Get my business")
def get_my_business(
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
    locale: str = Depends(deps.get_locale),
):
    if not current_user.business_id:
        raise HTTPException(
            status_code=404,
            detail=_("business_not_found", locale)
        )
    business = crud_business.get_business(db=db, business_id=current_user.business_id)
    if not business:
        raise HTTPException(
            status_code=404,
            detail=_("business_not_found", locale)
        )
    return BusinessOut.model_validate(business)
class LegacyVisitCurrencyOut(BaseModel):
    unknown_visits: int
    first_visit_at: Optional[datetime] = None
    last_visit_at: Optional[datetime] = None
    business_currency: Optional[str] = None


class LegacyVisitCurrencyConfirmIn(BaseModel):
    model_config = ConfigDict(extra='forbid')
    currency: Currency
    expected_count: int = Field(ge=0, strict=True)


class LegacyVisitCurrencyConfirmOut(BaseModel):
    currency: str
    updated_visits: int


def _owner_business_id(current_user: User, locale: str) -> int:
    # only the owner states what their history was; staff and platform admins don't
    if current_user.role != "business_owner":
        raise HTTPException(status_code=403, detail=_("not_allowed", locale))
    if not current_user.business_id:
        raise HTTPException(status_code=404, detail=_("business_not_found", locale))
    return current_user.business_id


@legacy_router.get("/me/legacy-visit-currency", response_model=LegacyVisitCurrencyOut,
                   summary="How many old visits have no saved currency")
def preview_legacy_visit_currency(
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
    locale: str = Depends(deps.get_locale),
):
    from app.api.core import legacy_visit_currency
    return LegacyVisitCurrencyOut(**legacy_visit_currency.preview(db, _owner_business_id(current_user, locale)))


@legacy_router.post("/me/legacy-visit-currency/confirm", response_model=LegacyVisitCurrencyConfirmOut,
                    summary="State the currency of old visits that have none",
                    responses={409: {'description': 'Count changed since the preview, nothing to confirm, or busy.'}})
def confirm_legacy_visit_currency(
    request: Request,
    payload: LegacyVisitCurrencyConfirmIn,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
    locale: str = Depends(deps.get_locale),
):
    from app.api.core import audit_service, legacy_visit_currency as legacy
    business_id = _owner_business_id(current_user, locale)
    try:
        count = legacy.confirm(db, business_id, payload.currency, payload.expected_count, actor=current_user,
                               request_id=audit_service.request_id_for(request))
        db.commit()
    except legacy.LegacyCurrencyCountChanged:
        db.rollback()
        raise HTTPException(409, detail={'code': 'legacy_currency_count_changed',
                                         'message': _('legacy_currency_count_changed', locale)}) from None
    except legacy.LegacyCurrencyNothingToConfirm:
        db.rollback()
        raise HTTPException(409, detail={'code': 'legacy_currency_nothing_to_confirm',
                                         'message': _('legacy_currency_nothing_to_confirm', locale)}) from None
    except legacy.LegacyCurrencyBusy:
        db.rollback()
        raise HTTPException(409, detail={'code': 'visit_price_retry', 'message': _('visit_price_retry', locale)}) from None
    except Exception:
        db.rollback()
        raise
    try:
        from app.api.core.redis_client import invalidate_analytics
        invalidate_analytics()
    except Exception:
        pass
    return LegacyVisitCurrencyConfirmOut(currency=payload.currency, updated_visits=count)


@router.get("/{business_id}", response_model=BusinessOut, summary="Get business by ID")
def get_business(
    business_id: int,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
    locale: str = Depends(deps.get_locale),
):
    business = crud_business.get_business(db=db, business_id=business_id)
    if not business:
        raise HTTPException(
            status_code=404,
            detail=_("business_not_found", locale)
        )
    if current_user.role != "admin" and current_user.business_id != business_id:
        raise HTTPException(status_code=403, detail=_("not_allowed", locale))
    return BusinessOut.model_validate(business)
@router.put("/{business_id}", response_model=BusinessOut, summary="Update business")
def update_business(
    business_id: int,
    business_update: BusinessUpdate,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
    locale: str = Depends(deps.get_locale),
):
    business = crud_business.get_business(db=db, business_id=business_id)
    if not business:
        raise HTTPException(
            status_code=404,
            detail=_("business_not_found", locale)
        )
    if current_user.role != "admin":
        from app.api.core.permissions import get_staff_record
        # the owner edits the business, staff of that business don't
        is_owner = current_user.business_id == business_id and current_user.role in ("business_owner", "specialist")
        if not is_owner or get_staff_record(db, current_user):
            raise HTTPException(status_code=403, detail=_("not_allowed", locale))
    updated_business = crud_business.update_business(
        db=db,
        business=business,
        business_update=business_update
    )
    return updated_business.__dict__ if hasattr(updated_business, '__dict__') else updated_business
@router.delete("/{business_id}", response_model=BusinessOut, summary="Delete business")
def delete_business(
    business_id: int,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
    locale: str = Depends(deps.get_locale),
):
    if current_user.role != "admin":
        raise HTTPException(status_code=403, detail=_("not_allowed", locale))
    business = crud_business.get_business(db=db, business_id=business_id)
    if not business:
        raise HTTPException(
            status_code=404,
            detail=_("business_not_found", locale)
        )
    deleted_business = crud_business.delete_business(db=db, business=business)
    return deleted_business.__dict__ if hasattr(deleted_business, '__dict__') else deleted_business


router.include_router(legacy_router)
