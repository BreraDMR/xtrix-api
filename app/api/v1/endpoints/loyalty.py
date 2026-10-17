from typing import Optional, List
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from app.api import deps
from app.api.core.i18n import _
from app.api.core.pagination import PaginatedResponse
from app.db.models.user import User
from app.schemas.loyalty import (
    LoyaltyProgramCreate,
    LoyaltyProgramUpdate,
    LoyaltyProgramOut,
    LoyaltyLevelCreate,
    LoyaltyLevelUpdate,
    LoyaltyLevelOut,
    LoyaltyPromotionCreate,
    LoyaltyPromotionUpdate,
    LoyaltyPromotionOut,
    LoyaltyTransactionOut,
    ClientLoyaltyBalance,
    UsePointsRequest,
    LoyaltyStatisticsResponse,
    LevelDistributionResponse,
    TopClientsResponse,
    PromotionEffectivenessResponse,
    PointsUsageTrendsResponse,
)
from app.api.crud import crud_loyalty
from app.api.core.logger import logger
from app.api.core.permissions import require_business_id
router = APIRouter()
@router.post("/program", response_model=LoyaltyProgramOut)
def create_loyalty_program(
    program: LoyaltyProgramCreate,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.role_required("admin", "business_owner")),
    locale: str = Depends(deps.get_locale),
):
    business_id = require_business_id(current_user, db, locale)
    if program.business_id != business_id:
        raise HTTPException(status_code=403, detail=_("not_allowed", locale))
    existing = crud_loyalty.get_loyalty_program(db, business_id)
    if existing:
        raise HTTPException(status_code=400, detail=_("loyalty_program_already_exists", locale))
    try:
        result = crud_loyalty.create_loyalty_program(db, program)
        logger.info(f"Created loyalty program {result.id} for business {business_id}")
        return result
    except ValueError as e:
        logger.error(f"Error creating loyalty program: {e}")
        raise HTTPException(status_code=400, detail=str(e))
@router.get("/program", response_model=LoyaltyProgramOut)
def get_loyalty_program(
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.role_required("admin", "business_owner", "specialist", "employee")),
    locale: str = Depends(deps.get_locale),
):
    business_id = require_business_id(current_user, db, locale)
    program = crud_loyalty.get_loyalty_program(db, business_id)
    if not program:
        raise HTTPException(status_code=404, detail=_("loyalty_program_not_found", locale))
    return program
@router.put("/program", response_model=LoyaltyProgramOut)
def update_loyalty_program(
    program_update: LoyaltyProgramUpdate,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.role_required("admin", "business_owner")),
    locale: str = Depends(deps.get_locale),
):
    business_id = require_business_id(current_user, db, locale)
    program = crud_loyalty.get_loyalty_program(db, business_id)
    if not program:
        raise HTTPException(status_code=404, detail=_("loyalty_program_not_found", locale))
    try:
        result = crud_loyalty.update_loyalty_program(db, program, program_update)
        logger.info(f"Updated loyalty program {result.id} for business {business_id}")
        return result
    except ValueError as e:
        logger.error(f"Error updating loyalty program: {e}")
        raise HTTPException(status_code=400, detail=str(e))
@router.delete("/program")
def delete_loyalty_program(
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.role_required("admin", "business_owner")),
    locale: str = Depends(deps.get_locale),
):
    business_id = require_business_id(current_user, db, locale)
    program = crud_loyalty.get_loyalty_program(db, business_id)
    if not program:
        raise HTTPException(status_code=404, detail=_("loyalty_program_not_found", locale))
    try:
        crud_loyalty.delete_loyalty_program(db, program.id)
        logger.info(f"Deleted loyalty program {program.id} for business {business_id}")
        return {"message": _("loyalty_program_deleted", locale)}
    except ValueError as e:
        logger.error(f"Error deleting loyalty program: {e}")
        raise HTTPException(status_code=400, detail=str(e))
@router.post("/levels", response_model=LoyaltyLevelOut)
def create_loyalty_level(
    level: LoyaltyLevelCreate,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.role_required("admin", "business_owner")),
    locale: str = Depends(deps.get_locale),
):
    business_id = require_business_id(current_user, db, locale)
    program = crud_loyalty.get_loyalty_program(db, business_id)
    if not program:
        raise HTTPException(status_code=404, detail=_("loyalty_program_not_found", locale))
    if level.program_id != program.id:
        raise HTTPException(status_code=403, detail=_("not_allowed", locale))
    try:
        result = crud_loyalty.create_loyalty_level(db, level)
        logger.info(f"Created loyalty level {result.id}")
        return result
    except ValueError as e:
        logger.error(f"Error creating loyalty level: {e}")
        raise HTTPException(status_code=400, detail=str(e))
@router.get("/levels", response_model=List[LoyaltyLevelOut])
def list_loyalty_levels(
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.role_required("admin", "business_owner", "specialist", "employee")),
    locale: str = Depends(deps.get_locale),
):
    business_id = require_business_id(current_user, db, locale)
    program = crud_loyalty.get_loyalty_program(db, business_id)
    if not program:
        return []
    levels = crud_loyalty.list_loyalty_levels(db, program_id=program.id)
    return levels
@router.put("/levels/{level_id}", response_model=LoyaltyLevelOut)
def update_loyalty_level(
    level_id: int,
    level_update: LoyaltyLevelUpdate,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.role_required("admin", "business_owner")),
    locale: str = Depends(deps.get_locale),
):
    business_id = require_business_id(current_user, db, locale)
    program = crud_loyalty.get_loyalty_program(db, business_id)
    if not program:
        raise HTTPException(status_code=404, detail=_("loyalty_program_not_found", locale))
    level = crud_loyalty.get_loyalty_level(db, level_id, program_id=program.id)
    if not level:
        raise HTTPException(status_code=404, detail=_("loyalty_level_not_found", locale))
    try:
        result = crud_loyalty.update_loyalty_level(db, level, level_update)
        return result
    except ValueError as e:
        logger.error(f"Error updating loyalty level: {e}")
        raise HTTPException(status_code=400, detail=str(e))
@router.delete("/levels/{level_id}")
def delete_loyalty_level(
    level_id: int,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.role_required("admin", "business_owner")),
    locale: str = Depends(deps.get_locale),
):
    business_id = require_business_id(current_user, db, locale)
    program = crud_loyalty.get_loyalty_program(db, business_id)
    if not program:
        raise HTTPException(status_code=404, detail=_("loyalty_program_not_found", locale))
    level = crud_loyalty.get_loyalty_level(db, level_id, program_id=program.id)
    if not level:
        raise HTTPException(status_code=404, detail=_("loyalty_level_not_found", locale))
    try:
        crud_loyalty.delete_loyalty_level(db, level_id)
        return {"message": _("loyalty_level_deleted", locale)}
    except ValueError as e:
        logger.error(f"Error deleting loyalty level: {e}")
        raise HTTPException(status_code=400, detail=str(e))
@router.post("/promotions", response_model=LoyaltyPromotionOut)
def create_loyalty_promotion(
    promotion: LoyaltyPromotionCreate,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.role_required("admin", "business_owner")),
    locale: str = Depends(deps.get_locale),
):
    business_id = require_business_id(current_user, db, locale)
    program = crud_loyalty.get_loyalty_program(db, business_id)
    if not program:
        raise HTTPException(status_code=404, detail=_("loyalty_program_not_found", locale))
    if promotion.program_id != program.id:
        raise HTTPException(status_code=403, detail=_("not_allowed", locale))
    try:
        result = crud_loyalty.create_loyalty_promotion(db, promotion)
        logger.info(f"Created loyalty promotion {result.id}")
        return result
    except ValueError as e:
        logger.error(f"Error creating loyalty promotion: {e}")
        raise HTTPException(status_code=400, detail=str(e))
@router.get("/promotions", response_model=List[LoyaltyPromotionOut])
def list_loyalty_promotions(
    client_id: Optional[int] = Query(None),
    is_active: Optional[bool] = Query(True),
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.role_required("admin", "business_owner", "specialist", "employee")),
    locale: str = Depends(deps.get_locale),
):
    business_id = require_business_id(current_user, db, locale)
    program = crud_loyalty.get_loyalty_program(db, business_id)
    if not program:
        return []
    promotions = crud_loyalty.list_loyalty_promotions(
        db, program_id=program.id, client_id=client_id, is_active=is_active
    )
    return promotions
@router.put("/promotions/{promotion_id}", response_model=LoyaltyPromotionOut)
def update_loyalty_promotion(
    promotion_id: int,
    promotion_update: LoyaltyPromotionUpdate,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.role_required("admin", "business_owner")),
    locale: str = Depends(deps.get_locale),
):
    business_id = require_business_id(current_user, db, locale)
    program = crud_loyalty.get_loyalty_program(db, business_id)
    if not program:
        raise HTTPException(status_code=404, detail=_("loyalty_program_not_found", locale))
    promotion = crud_loyalty.get_loyalty_promotion(db, promotion_id, program_id=program.id)
    if not promotion:
        raise HTTPException(status_code=404, detail=_("loyalty_promotion_not_found", locale))
    try:
        result = crud_loyalty.update_loyalty_promotion(db, promotion, promotion_update)
        return result
    except ValueError as e:
        logger.error(f"Error updating loyalty promotion: {e}")
        raise HTTPException(status_code=400, detail=str(e))
@router.delete("/promotions/{promotion_id}")
def delete_loyalty_promotion(
    promotion_id: int,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.role_required("admin", "business_owner")),
    locale: str = Depends(deps.get_locale),
):
    business_id = require_business_id(current_user, db, locale)
    program = crud_loyalty.get_loyalty_program(db, business_id)
    if not program:
        raise HTTPException(status_code=404, detail=_("loyalty_program_not_found", locale))
    promotion = crud_loyalty.get_loyalty_promotion(db, promotion_id, program_id=program.id)
    if not promotion:
        raise HTTPException(status_code=404, detail=_("loyalty_promotion_not_found", locale))
    try:
        crud_loyalty.delete_loyalty_promotion(db, promotion_id)
        return {"message": _("loyalty_promotion_deleted", locale)}
    except ValueError as e:
        logger.error(f"Error deleting loyalty promotion: {e}")
        raise HTTPException(status_code=400, detail=str(e))
@router.get("/balance/{client_id}", response_model=ClientLoyaltyBalance)
def get_client_loyalty_balance(
    client_id: int,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.role_required("admin", "business_owner", "specialist", "employee")),
    locale: str = Depends(deps.get_locale),
):
    business_id = require_business_id(current_user, db, locale)
    relation = crud_loyalty.get_client_loyalty_balance(db, client_id, business_id)
    if not relation:
        return ClientLoyaltyBalance(
            client_id=client_id,
            business_id=business_id,
            points=0.0,
            cashback=0.0,
            level=None,
            level_id=None,
        )
    level = None
    if relation.loyalty_level_id:
        from app.db.models.loyalty_program import LoyaltyLevel
        level_obj = db.query(LoyaltyLevel).filter(LoyaltyLevel.id == relation.loyalty_level_id).first()
        if level_obj:
            level = LoyaltyLevelOut.model_validate(level_obj)
    return ClientLoyaltyBalance(
        client_id=relation.client_id,
        business_id=relation.business_id,
        points=relation.loyalty_points,
        cashback=relation.loyalty_cashback,
        level=level,
        level_id=relation.loyalty_level_id,
    )
@router.get("/transactions/{client_id}", response_model=PaginatedResponse[LoyaltyTransactionOut])
def list_client_transactions(
    client_id: int,
    transaction_type: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.role_required("admin", "business_owner", "specialist", "employee")),
    locale: str = Depends(deps.get_locale),
):
    business_id = require_business_id(current_user, db, locale)
    skip = (page - 1) * per_page
    transactions = crud_loyalty.list_loyalty_transactions(
        db,
        client_id=client_id,
        business_id=business_id,
        transaction_type=transaction_type,
        skip=skip,
        limit=per_page,
    )
    from app.db.models.loyalty_program import LoyaltyTransaction
    total_query = db.query(LoyaltyTransaction).filter(
        LoyaltyTransaction.client_id == client_id,
        LoyaltyTransaction.business_id == business_id,
    )
    if transaction_type:
        total_query = total_query.filter(LoyaltyTransaction.transaction_type == transaction_type)
    total = total_query.count()
    return PaginatedResponse.create(
        items=transactions,
        total=total,
        page=page,
        per_page=per_page,
    )
@router.post("/use-points", response_model=dict)
def use_points(
    request: UsePointsRequest,
    client_id: int,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.role_required("admin", "business_owner", "specialist", "employee")),
    locale: str = Depends(deps.get_locale),
):
    business_id = require_business_id(current_user, db, locale)
    visit_amount = None
    if request.visit_id:
        from app.api.crud import crud_visit
        visit = crud_visit.get_visit(db, request.visit_id)
        if visit:
            visit_amount = visit.price
    try:
        from app.api.core.loyalty_logic import use_points_for_visit
        success, discount_amount, error_message = use_points_for_visit(
            db,
            client_id=client_id,
            business_id=business_id,
            points_to_use=request.points_to_use or 0.0,
            cashback_to_use=request.cashback_to_use or 0.0,
            visit_id=request.visit_id,
            visit_amount=visit_amount,
        )
        if not success:
            raise HTTPException(status_code=400, detail=error_message)
        return {
            "message": _("points_used_successfully", locale),
            "discount_amount": discount_amount,
        }
    except HTTPException:
        raise
    except ValueError as e:
        logger.error(f"Error using loyalty points: {e}")
        raise HTTPException(status_code=400, detail=str(e))
@router.get("/analytics/statistics", response_model=LoyaltyStatisticsResponse)
def get_loyalty_statistics(
    date_from: Optional[datetime] = Query(None),
    date_to: Optional[datetime] = Query(None),
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.role_required("admin", "business_owner", "specialist", "employee")),
    locale: str = Depends(deps.get_locale),
):
    business_id = require_business_id(current_user, db, locale)
    from app.api.core.loyalty_analytics import get_loyalty_statistics
    stats = get_loyalty_statistics(db, business_id, date_from, date_to)
    return LoyaltyStatisticsResponse(**stats)
@router.get("/analytics/level-distribution", response_model=LevelDistributionResponse)
def get_level_distribution(
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.role_required("admin", "business_owner", "specialist", "employee")),
    locale: str = Depends(deps.get_locale),
):
    business_id = require_business_id(current_user, db, locale)
    from app.api.core.loyalty_analytics import get_level_distribution
    distribution = get_level_distribution(db, business_id)
    return LevelDistributionResponse(distribution=distribution)
@router.get("/analytics/top-clients", response_model=TopClientsResponse)
def get_top_clients(
    limit: int = Query(10, ge=1, le=100),
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.role_required("admin", "business_owner", "specialist", "employee")),
    locale: str = Depends(deps.get_locale),
):
    business_id = require_business_id(current_user, db, locale)
    from app.api.core.loyalty_analytics import get_top_clients_by_points
    clients = get_top_clients_by_points(db, business_id, limit)
    return TopClientsResponse(clients=clients)
@router.get("/analytics/promotion-effectiveness", response_model=PromotionEffectivenessResponse)
def get_promotion_effectiveness(
    date_from: Optional[datetime] = Query(None),
    date_to: Optional[datetime] = Query(None),
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.role_required("admin", "business_owner", "specialist", "employee")),
    locale: str = Depends(deps.get_locale),
):
    business_id = require_business_id(current_user, db, locale)
    from app.api.core.loyalty_analytics import get_promotion_effectiveness
    promotions = get_promotion_effectiveness(db, business_id, date_from, date_to)
    return PromotionEffectivenessResponse(promotions=promotions)
@router.get("/analytics/trends", response_model=PointsUsageTrendsResponse)
def get_points_usage_trends(
    date_from: Optional[datetime] = Query(None),
    date_to: Optional[datetime] = Query(None),
    group_by: str = Query("day", pattern="^(day|week|month)$"),
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.role_required("admin", "business_owner", "specialist", "employee")),
    locale: str = Depends(deps.get_locale),
):
    business_id = require_business_id(current_user, db, locale)
    from app.api.core.loyalty_analytics import get_points_usage_trends
    trends = get_points_usage_trends(db, business_id, date_from, date_to, group_by)
    return PointsUsageTrendsResponse(trends=trends)
