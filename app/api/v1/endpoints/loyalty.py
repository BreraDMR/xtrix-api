"""Loyalty program endpoints."""
from typing import Optional, List
from datetime import date, datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api import deps
from app.api.core.i18n import _, localize_response
from app.api.core.pagination import PaginationParams, PaginatedResponse
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
)
from app.api.crud import crud_loyalty
from app.api.core.logger import logger

router = APIRouter()


def get_user_business_id(current_user: User, db: Session) -> Optional[int]:
    """Получить business_id текущего пользователя."""
    if hasattr(current_user, 'business_id') and current_user.business_id:
        return current_user.business_id
    
    if current_user.role == "employee":
        from app.api.crud import crud_employee
        employee = crud_employee.get_employee_by_user_id(db, current_user.id)
        if employee and employee.business_id:
            return employee.business_id
    
    return None


# ============ LOYALTY PROGRAM ============

@router.post("/program", response_model=LoyaltyProgramOut)
def create_loyalty_program(
    program: LoyaltyProgramCreate,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.role_required("admin", "business_owner")),
    locale: str = Depends(deps.get_locale),
):
    """Create loyalty program."""
    business_id = get_user_business_id(current_user, db)
    if not business_id:
        raise HTTPException(status_code=403, detail=_("business_required", locale))
    
    if program.business_id != business_id:
        raise HTTPException(status_code=403, detail=_("not_allowed", locale))
    
    # Check if program already exists
    existing = crud_loyalty.get_loyalty_program(db, business_id)
    if existing:
        raise HTTPException(status_code=400, detail=_("loyalty_program_already_exists", locale))
    
    try:
        result = crud_loyalty.create_loyalty_program(db, program)
        logger.info(f"Created loyalty program {result.id} for business {business_id}")
        return result
    except Exception as e:
        logger.error(f"Error creating loyalty program: {e}")
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/program", response_model=LoyaltyProgramOut)
def get_loyalty_program(
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.role_required("admin", "business_owner", "specialist", "employee")),
    locale: str = Depends(deps.get_locale),
):
    """Get loyalty program for current business."""
    business_id = get_user_business_id(current_user, db)
    if not business_id:
        raise HTTPException(status_code=403, detail=_("business_required", locale))
    
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
    """Update loyalty program."""
    business_id = get_user_business_id(current_user, db)
    if not business_id:
        raise HTTPException(status_code=403, detail=_("business_required", locale))
    
    program = crud_loyalty.get_loyalty_program(db, business_id)
    if not program:
        raise HTTPException(status_code=404, detail=_("loyalty_program_not_found", locale))
    
    try:
        result = crud_loyalty.update_loyalty_program(db, program, program_update)
        logger.info(f"Updated loyalty program {result.id} for business {business_id}")
        return result
    except Exception as e:
        logger.error(f"Error updating loyalty program: {e}")
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/program")
def delete_loyalty_program(
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.role_required("admin", "business_owner")),
    locale: str = Depends(deps.get_locale),
):
    """Delete loyalty program."""
    business_id = get_user_business_id(current_user, db)
    if not business_id:
        raise HTTPException(status_code=403, detail=_("business_required", locale))
    
    program = crud_loyalty.get_loyalty_program(db, business_id)
    if not program:
        raise HTTPException(status_code=404, detail=_("loyalty_program_not_found", locale))
    
    try:
        crud_loyalty.delete_loyalty_program(db, program.id)
        logger.info(f"Deleted loyalty program {program.id} for business {business_id}")
        return {"message": _("loyalty_program_deleted", locale)}
    except Exception as e:
        logger.error(f"Error deleting loyalty program: {e}")
        raise HTTPException(status_code=400, detail=str(e))


# ============ LOYALTY LEVELS ============

@router.post("/levels", response_model=LoyaltyLevelOut)
def create_loyalty_level(
    level: LoyaltyLevelCreate,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.role_required("admin", "business_owner")),
    locale: str = Depends(deps.get_locale),
):
    """Create loyalty level."""
    business_id = get_user_business_id(current_user, db)
    if not business_id:
        raise HTTPException(status_code=403, detail=_("business_required", locale))
    
    program = crud_loyalty.get_loyalty_program(db, business_id)
    if not program:
        raise HTTPException(status_code=404, detail=_("loyalty_program_not_found", locale))
    
    if level.program_id != program.id:
        raise HTTPException(status_code=403, detail=_("not_allowed", locale))
    
    try:
        result = crud_loyalty.create_loyalty_level(db, level)
        logger.info(f"Created loyalty level {result.id}")
        return result
    except Exception as e:
        logger.error(f"Error creating loyalty level: {e}")
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/levels", response_model=List[LoyaltyLevelOut])
def list_loyalty_levels(
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.role_required("admin", "business_owner", "specialist", "employee")),
    locale: str = Depends(deps.get_locale),
):
    """List loyalty levels."""
    business_id = get_user_business_id(current_user, db)
    if not business_id:
        raise HTTPException(status_code=403, detail=_("business_required", locale))
    
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
    """Update loyalty level."""
    business_id = get_user_business_id(current_user, db)
    if not business_id:
        raise HTTPException(status_code=403, detail=_("business_required", locale))
    
    program = crud_loyalty.get_loyalty_program(db, business_id)
    if not program:
        raise HTTPException(status_code=404, detail=_("loyalty_program_not_found", locale))
    
    level = crud_loyalty.get_loyalty_level(db, level_id, program_id=program.id)
    if not level:
        raise HTTPException(status_code=404, detail=_("loyalty_level_not_found", locale))
    
    try:
        result = crud_loyalty.update_loyalty_level(db, level, level_update)
        return result
    except Exception as e:
        logger.error(f"Error updating loyalty level: {e}")
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/levels/{level_id}")
def delete_loyalty_level(
    level_id: int,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.role_required("admin", "business_owner")),
    locale: str = Depends(deps.get_locale),
):
    """Delete loyalty level."""
    business_id = get_user_business_id(current_user, db)
    if not business_id:
        raise HTTPException(status_code=403, detail=_("business_required", locale))
    
    program = crud_loyalty.get_loyalty_program(db, business_id)
    if not program:
        raise HTTPException(status_code=404, detail=_("loyalty_program_not_found", locale))
    
    level = crud_loyalty.get_loyalty_level(db, level_id, program_id=program.id)
    if not level:
        raise HTTPException(status_code=404, detail=_("loyalty_level_not_found", locale))
    
    try:
        crud_loyalty.delete_loyalty_level(db, level_id)
        return {"message": _("loyalty_level_deleted", locale)}
    except Exception as e:
        logger.error(f"Error deleting loyalty level: {e}")
        raise HTTPException(status_code=400, detail=str(e))


# ============ LOYALTY PROMOTIONS ============

@router.post("/promotions", response_model=LoyaltyPromotionOut)
def create_loyalty_promotion(
    promotion: LoyaltyPromotionCreate,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.role_required("admin", "business_owner")),
    locale: str = Depends(deps.get_locale),
):
    """Create loyalty promotion."""
    business_id = get_user_business_id(current_user, db)
    if not business_id:
        raise HTTPException(status_code=403, detail=_("business_required", locale))
    
    program = crud_loyalty.get_loyalty_program(db, business_id)
    if not program:
        raise HTTPException(status_code=404, detail=_("loyalty_program_not_found", locale))
    
    if promotion.program_id != program.id:
        raise HTTPException(status_code=403, detail=_("not_allowed", locale))
    
    try:
        result = crud_loyalty.create_loyalty_promotion(db, promotion)
        logger.info(f"Created loyalty promotion {result.id}")
        return result
    except Exception as e:
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
    """List loyalty promotions."""
    business_id = get_user_business_id(current_user, db)
    if not business_id:
        raise HTTPException(status_code=403, detail=_("business_required", locale))
    
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
    """Update loyalty promotion."""
    business_id = get_user_business_id(current_user, db)
    if not business_id:
        raise HTTPException(status_code=403, detail=_("business_required", locale))
    
    program = crud_loyalty.get_loyalty_program(db, business_id)
    if not program:
        raise HTTPException(status_code=404, detail=_("loyalty_program_not_found", locale))
    
    promotion = crud_loyalty.get_loyalty_promotion(db, promotion_id, program_id=program.id)
    if not promotion:
        raise HTTPException(status_code=404, detail=_("loyalty_promotion_not_found", locale))
    
    try:
        result = crud_loyalty.update_loyalty_promotion(db, promotion, promotion_update)
        return result
    except Exception as e:
        logger.error(f"Error updating loyalty promotion: {e}")
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/promotions/{promotion_id}")
def delete_loyalty_promotion(
    promotion_id: int,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.role_required("admin", "business_owner")),
    locale: str = Depends(deps.get_locale),
):
    """Delete loyalty promotion."""
    business_id = get_user_business_id(current_user, db)
    if not business_id:
        raise HTTPException(status_code=403, detail=_("business_required", locale))
    
    program = crud_loyalty.get_loyalty_program(db, business_id)
    if not program:
        raise HTTPException(status_code=404, detail=_("loyalty_program_not_found", locale))
    
    promotion = crud_loyalty.get_loyalty_promotion(db, promotion_id, program_id=program.id)
    if not promotion:
        raise HTTPException(status_code=404, detail=_("loyalty_promotion_not_found", locale))
    
    try:
        crud_loyalty.delete_loyalty_promotion(db, promotion_id)
        return {"message": _("loyalty_promotion_deleted", locale)}
    except Exception as e:
        logger.error(f"Error deleting loyalty promotion: {e}")
        raise HTTPException(status_code=400, detail=str(e))


# ============ CLIENT BALANCE ============

@router.get("/balance/{client_id}", response_model=ClientLoyaltyBalance)
def get_client_loyalty_balance(
    client_id: int,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.role_required("admin", "business_owner", "specialist", "employee")),
    locale: str = Depends(deps.get_locale),
):
    """Get client loyalty balance."""
    business_id = get_user_business_id(current_user, db)
    if not business_id:
        raise HTTPException(status_code=403, detail=_("business_required", locale))
    
    relation = crud_loyalty.get_client_loyalty_balance(db, client_id, business_id)
    if not relation:
        # Return zero balance
        from app.schemas.loyalty import ClientLoyaltyBalance
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


# ============ TRANSACTIONS ============

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
    """List client loyalty transactions."""
    business_id = get_user_business_id(current_user, db)
    if not business_id:
        raise HTTPException(status_code=403, detail=_("business_required", locale))
    
    skip = (page - 1) * per_page
    transactions = crud_loyalty.list_loyalty_transactions(
        db,
        client_id=client_id,
        business_id=business_id,
        transaction_type=transaction_type,
        skip=skip,
        limit=per_page,
    )
    
    # Get total count
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


# ============ USE POINTS ============

@router.post("/use-points", response_model=dict)
def use_points(
    request: UsePointsRequest,
    client_id: int,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.role_required("admin", "business_owner", "specialist", "employee")),
    locale: str = Depends(deps.get_locale),
):
    """Use loyalty points/cashback for payment."""
    business_id = get_user_business_id(current_user, db)
    if not business_id:
        raise HTTPException(status_code=403, detail=_("business_required", locale))
    
    # Get visit amount if visit_id provided
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
    except Exception as e:
        logger.error(f"Error using loyalty points: {e}")
        raise HTTPException(status_code=400, detail=str(e))
