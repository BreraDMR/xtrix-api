from typing import List, Optional
from datetime import date
from sqlalchemy.orm import Session
from sqlalchemy import and_, or_, inspect
from sqlalchemy.dialects.postgresql import insert
from app.api.core import cashback_wallets
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
    program = LoyaltyProgram(**program_in.model_dump())
    db.add(program)
    db.commit()
    db.refresh(program)
    return program
def get_loyalty_program(db: Session, business_id: int) -> Optional[LoyaltyProgram]:
    return db.query(LoyaltyProgram).filter(LoyaltyProgram.business_id == business_id).first()
def lock_loyalty_program(db: Session, business_id: int, program_id: Optional[int] = None):
    with db.no_autoflush:
        if any(isinstance(row, LoyaltyProgram) and row.business_id == business_id
               for row in db.new.union(db.dirty).union(db.deleted)):
            raise cashback_wallets.CashbackError('visit_price_retry')
        query = db.query(LoyaltyProgram).filter(LoyaltyProgram.business_id == business_id)
        if program_id is not None:
            query = query.filter(LoyaltyProgram.id == program_id)
        return query.populate_existing().with_for_update(read=True).one_or_none()


def update_loyalty_program(
    db: Session, program: LoyaltyProgram, program_in: LoyaltyProgramUpdate
) -> LoyaltyProgram:
    update_data = program_in.model_dump(exclude_unset=True)
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
    level = LoyaltyLevel(**level_in.model_dump())
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
    update_data = level_in.model_dump(exclude_unset=True)
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
    promotion = LoyaltyPromotion(**promotion_in.model_dump())
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
    update_data = promotion_in.model_dump(exclude_unset=True)
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
def lock_loyalty_visit(db: Session, visit_id: int, client_id: int, business_id: int):
    """Take the visit anchor before calculating or changing any linked balance."""
    from app.db.models.visit import Visit
    from app.db.models.visit_item import VisitItem
    with db.no_autoflush:
        if any(isinstance(row, VisitItem) for row in db.new.union(db.dirty).union(db.deleted)):
            raise ValueError('visit_price_retry')
        cashback_wallets.lock_money_references(db, client_id, business_id)
        if any(isinstance(row, Visit) and row.id == visit_id for row in db.new.union(db.dirty).union(db.deleted)):
            raise ValueError('visit_price_retry')
        visit = db.query(Visit).filter(Visit.id == visit_id, Visit.client_id == client_id,
            Visit.business_id == business_id, Visit.deleted_at.is_(None)).populate_existing().with_for_update().one_or_none()
        if visit is None:
            raise ValueError('visit_not_found')
        return visit


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
    cashback_currency: Optional[str] = None,
    discount_amount=None,
) -> LoyaltyTransaction:
    try:
        return _write_loyalty_transaction(db, client_id, business_id, transaction_type, points_amount,
            cashback_amount, program_id, visit_id, promotion_id, level_id, description, expiry_date, cashback_currency, discount_amount)
    except Exception:
        db.rollback()
        raise


def _write_loyalty_transaction(db, client_id, business_id, transaction_type, points_amount,
    cashback_amount, program_id, visit_id, promotion_id, level_id, description, expiry_date, cashback_currency, discount_amount):
    if visit_id is not None:
        locked_visit = lock_loyalty_visit(db, visit_id, client_id, business_id)
        if transaction_type == 'spent' and db.query(LoyaltyTransaction.id).filter(
                LoyaltyTransaction.visit_id == visit_id, LoyaltyTransaction.business_id == business_id,
                LoyaltyTransaction.client_id == client_id, LoyaltyTransaction.transaction_type == 'spent',
                or_(LoyaltyTransaction.points_amount != 0, LoyaltyTransaction.cashback_amount != 0)).first() is not None:
            raise cashback_wallets.CashbackError('loyalty_payment_already_recorded')
        if transaction_type == 'spent':
            from app.api.core.loyalty_settlement_guard import ensure_spend_allowed
            ensure_spend_allowed(db, visit_id, business_id)
        if cashback_amount:
            if cashback_currency is not None and cashback_currency != locked_visit.currency_snapshot:
                raise cashback_wallets.CashbackError('loyalty_cashback_currency_unavailable')
            cashback_currency = locked_visit.currency_snapshot
    else:
        if transaction_type == 'spent' and (points_amount or cashback_amount):
            raise cashback_wallets.CashbackError('loyalty_cashback_visit_required')
        cashback_wallets.lock_money_references(db, client_id, business_id)
    with db.no_autoflush:
        relation = (
            db.query(ClientBusinessRelation)
            .filter(ClientBusinessRelation.client_id == client_id,
                    ClientBusinessRelation.business_id == business_id)
            .with_for_update().first()
        )
        if relation is not None:
            state = inspect(relation)
            if any(state.attrs[name].history.has_changes() for name in ('loyalty_points', 'loyalty_cashback')):
                raise ValueError('visit_price_retry')
            # Keep pending nonfinancial relation edits, but use the locked balance.
            db.refresh(relation, attribute_names=['loyalty_points', 'loyalty_cashback'])
    if relation is None:
        # An absent row cannot be locked. Two first awards may arrive together.
        with db.no_autoflush:
            db.execute(insert(ClientBusinessRelation).values(client_id=client_id, business_id=business_id,
                loyalty_points=0.0, loyalty_cashback=0.0).on_conflict_do_nothing(
                    index_elements=['client_id', 'business_id']))
            relation = db.query(ClientBusinessRelation).filter_by(client_id=client_id,
                business_id=business_id).populate_existing().with_for_update().one()
    new_points_balance = relation.loyalty_points + points_amount
    if transaction_type == 'spent' and new_points_balance < 0:
        raise ValueError('loyalty_balance_insufficient')
    discount_unit = None
    saved_discount = None
    if transaction_type == 'spent' and (points_amount or cashback_amount):
        if points_amount > 0 or (cashback_amount or 0) > 0:
            raise cashback_wallets.CashbackError('loyalty_payment_quote_required')
        discount_unit = cashback_wallets._unit(locked_visit.currency_snapshot)
        cash = cashback_wallets._amount(cashback_amount or 0).copy_negate()
        if discount_amount is None:
            if points_amount:
                raise cashback_wallets.CashbackError('loyalty_payment_quote_required')
            discount_amount = cash
        saved_discount = cashback_wallets._amount(discount_amount)
        quote = cashback_wallets._amount(locked_visit.price)
        if saved_discount <= 0 or saved_discount < cash or saved_discount > quote:
            raise cashback_wallets.CashbackError('loyalty_payment_quote_required')
    elif discount_amount is not None:
        raise cashback_wallets.CashbackError('loyalty_payment_quote_required')
    movement = (cashback_wallets.move_balance(db, client_id, business_id, cashback_currency, cashback_amount)
                if cashback_amount else None)
    new_cashback_balance = float(movement.balance_after) if movement is not None else 0.0
    relation.loyalty_points = new_points_balance
    # The legacy untyped cashback is evidence, never a source for known-unit spending.
    transaction = LoyaltyTransaction(
        client_id=client_id,
        business_id=business_id,
        program_id=program_id,
        transaction_type=transaction_type,
        points_amount=points_amount,
        cashback_amount=cashback_amount or 0.0,
        points_balance_after=new_points_balance,
        cashback_balance_after=new_cashback_balance,
        discount_currency=discount_unit,
        discount_amount_exact=saved_discount,
        cashback_currency=movement.currency if movement is not None else None,
        cashback_amount_exact=movement.amount if movement is not None else None,
        cashback_balance_after_exact=movement.balance_after if movement is not None else None,
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
