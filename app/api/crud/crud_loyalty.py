from typing import List, Optional
from datetime import date
from sqlalchemy.orm import Session
from sqlalchemy import and_, or_
from app.db.models.loyalty_program import (
    LoyaltyProgram,
    LoyaltyLevel,
    LoyaltyPromotion,
    LoyaltyTransaction,
)
from app.db.models.client_business_relation import ClientBusinessRelation
from app.schemas.loyalty import (
    LoyaltyProgramCreate,
    LoyaltyProgramUpdate,
    LoyaltyLevelCreate,
    LoyaltyLevelUpdate,
    LoyaltyPromotionCreate,
    LoyaltyPromotionUpdate,
)
def create_loyalty_program(db: Session, program_in: LoyaltyProgramCreate) -> LoyaltyProgram:
    program = LoyaltyProgram(**program_in.dict())
    db.add(program)
    db.commit()
    db.refresh(program)
    return program
def get_loyalty_program(db: Session, business_id: int) -> Optional[LoyaltyProgram]:
    return db.query(LoyaltyProgram).filter(LoyaltyProgram.business_id == business_id).first()
def update_loyalty_program(
    db: Session, program: LoyaltyProgram, program_in: LoyaltyProgramUpdate
) -> LoyaltyProgram:
    update_data = program_in.dict(exclude_unset=True)
    for field, value in update_data.items():
        setattr(program, field, value)
    db.commit()
    db.refresh(program)
    return program
def delete_loyalty_program(db: Session, program_id: int) -> bool:
    program = db.query(LoyaltyProgram).filter(LoyaltyProgram.id == program_id).first()
    if not program:
        return False
    db.delete(program)
    db.commit()
    return True
def create_loyalty_level(db: Session, level_in: LoyaltyLevelCreate) -> LoyaltyLevel:
    level = LoyaltyLevel(**level_in.dict())
    db.add(level)
    db.commit()
    db.refresh(level)
    return level
def get_loyalty_level(db: Session, level_id: int, program_id: Optional[int] = None) -> Optional[LoyaltyLevel]:
    query = db.query(LoyaltyLevel).filter(LoyaltyLevel.id == level_id)
    if program_id:
        query = query.filter(LoyaltyLevel.program_id == program_id)
    return query.first()
def list_loyalty_levels(
    db: Session, program_id: int, skip: int = 0, limit: int = 100
) -> List[LoyaltyLevel]:
    return (
        db.query(LoyaltyLevel)
        .filter(LoyaltyLevel.program_id == program_id)
        .order_by(LoyaltyLevel.level_number)
        .offset(skip)
        .limit(limit)
        .all()
    )
def update_loyalty_level(
    db: Session, level: LoyaltyLevel, level_in: LoyaltyLevelUpdate
) -> LoyaltyLevel:
    update_data = level_in.dict(exclude_unset=True)
    for field, value in update_data.items():
        setattr(level, field, value)
    db.commit()
    db.refresh(level)
    return level
def delete_loyalty_level(db: Session, level_id: int) -> bool:
    level = db.query(LoyaltyLevel).filter(LoyaltyLevel.id == level_id).first()
    if not level:
        return False
    db.delete(level)
    db.commit()
    return True
def create_loyalty_promotion(
    db: Session, promotion_in: LoyaltyPromotionCreate
) -> LoyaltyPromotion:
    promotion = LoyaltyPromotion(**promotion_in.dict())
    db.add(promotion)
    db.commit()
    db.refresh(promotion)
    return promotion
def get_loyalty_promotion(
    db: Session, promotion_id: int, program_id: Optional[int] = None
) -> Optional[LoyaltyPromotion]:
    query = db.query(LoyaltyPromotion).filter(LoyaltyPromotion.id == promotion_id)
    if program_id:
        query = query.filter(LoyaltyPromotion.program_id == program_id)
    return query.first()
def list_loyalty_promotions(
    db: Session,
    program_id: Optional[int] = None,
    client_id: Optional[int] = None,
    is_active: Optional[bool] = True,
    skip: int = 0,
    limit: int = 100,
) -> List[LoyaltyPromotion]:
    query = db.query(LoyaltyPromotion)
    if program_id:
        query = query.filter(LoyaltyPromotion.program_id == program_id)
    if client_id is not None:
        query = query.filter(
            or_(
                LoyaltyPromotion.client_id == client_id,
                LoyaltyPromotion.client_id.is_(None)
            )
        )
    if is_active is not None:
        query = query.filter(LoyaltyPromotion.is_active == is_active)
    today = date.today()
    query = query.filter(
        and_(
            LoyaltyPromotion.start_date <= today,
            LoyaltyPromotion.end_date >= today
        )
    )
    return query.order_by(LoyaltyPromotion.start_date.desc()).offset(skip).limit(limit).all()
def update_loyalty_promotion(
    db: Session, promotion: LoyaltyPromotion, promotion_in: LoyaltyPromotionUpdate
) -> LoyaltyPromotion:
    update_data = promotion_in.dict(exclude_unset=True)
    for field, value in update_data.items():
        setattr(promotion, field, value)
    db.commit()
    db.refresh(promotion)
    return promotion
def delete_loyalty_promotion(db: Session, promotion_id: int) -> bool:
    promotion = db.query(LoyaltyPromotion).filter(LoyaltyPromotion.id == promotion_id).first()
    if not promotion:
        return False
    db.delete(promotion)
    db.commit()
    return True
def create_loyalty_transaction(
    db: Session,
    client_id: int,
    business_id: int,
    transaction_type: str,
    points_amount: float,
    cashback_amount: Optional[float] = None,
    program_id: Optional[int] = None,
    visit_id: Optional[int] = None,
    promotion_id: Optional[int] = None,
    level_id: Optional[int] = None,
    description: Optional[str] = None,
    expiry_date: Optional[date] = None,
) -> LoyaltyTransaction:
    relation = (
        db.query(ClientBusinessRelation)
        .filter(
            and_(
                ClientBusinessRelation.client_id == client_id,
                ClientBusinessRelation.business_id == business_id
            )
        )
        .first()
    )
    if not relation:
        relation = ClientBusinessRelation(
            client_id=client_id,
            business_id=business_id,
            loyalty_points=0.0,
            loyalty_cashback=0.0,
        )
        db.add(relation)
        db.flush()
    new_points_balance = relation.loyalty_points + points_amount
    new_cashback_balance = relation.loyalty_cashback + (cashback_amount or 0.0)
    relation.loyalty_points = new_points_balance
    relation.loyalty_cashback = new_cashback_balance
    transaction = LoyaltyTransaction(
        client_id=client_id,
        business_id=business_id,
        program_id=program_id,
        transaction_type=transaction_type,
        points_amount=points_amount,
        cashback_amount=cashback_amount or 0.0,
        points_balance_after=new_points_balance,
        cashback_balance_after=new_cashback_balance,
        visit_id=visit_id,
        promotion_id=promotion_id,
        level_id=level_id,
        description=description,
        expiry_date=expiry_date,
    )
    db.add(transaction)
    db.commit()
    db.refresh(transaction)
    return transaction
def list_loyalty_transactions(
    db: Session,
    client_id: Optional[int] = None,
    business_id: Optional[int] = None,
    transaction_type: Optional[str] = None,
    skip: int = 0,
    limit: int = 100,
) -> List[LoyaltyTransaction]:
    query = db.query(LoyaltyTransaction)
    if client_id:
        query = query.filter(LoyaltyTransaction.client_id == client_id)
    if business_id:
        query = query.filter(LoyaltyTransaction.business_id == business_id)
    if transaction_type:
        query = query.filter(LoyaltyTransaction.transaction_type == transaction_type)
    return query.order_by(LoyaltyTransaction.created_at.desc()).offset(skip).limit(limit).all()
def get_client_loyalty_balance(
    db: Session, client_id: int, business_id: int
) -> Optional[ClientBusinessRelation]:
    return (
        db.query(ClientBusinessRelation)
        .filter(
            and_(
                ClientBusinessRelation.client_id == client_id,
                ClientBusinessRelation.business_id == business_id
            )
        )
        .first()
    )
