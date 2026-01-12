"""Loyalty program logic for automatic bonus calculation and award."""
from datetime import date, datetime, timedelta
from typing import Optional, Dict, Any, Tuple, List
from sqlalchemy.orm import Session

from app.db.models.loyalty_program import (
    LoyaltyProgram,
    LoyaltyLevel,
    LoyaltyPromotion,
    LoyaltyTransaction,
)
from app.db.models.client_business_relation import ClientBusinessRelation
from app.db.models.visit import Visit
from app.db.models.client import Client
from app.api.crud import crud_loyalty
from app.api.core.logger import logger
import json


def get_active_promotions(
    db: Session,
    program_id: int,
    client_id: Optional[int] = None,
    service_id: Optional[int] = None,
    amount: Optional[float] = None,
) -> List[LoyaltyPromotion]:
    """Get active promotions that apply to a transaction."""
    today = date.today()
    
    query = db.query(LoyaltyPromotion).filter(
        LoyaltyPromotion.program_id == program_id,
        LoyaltyPromotion.is_active == True,
        LoyaltyPromotion.start_date <= today,
        LoyaltyPromotion.end_date >= today,
    )
    
    # Filter by client (null = for all clients, or specific client_id)
    if client_id:
        query = query.filter(
            or_(
                LoyaltyPromotion.client_id == client_id,
                LoyaltyPromotion.client_id.is_(None)
            )
        )
    else:
        query = query.filter(LoyaltyPromotion.client_id.is_(None))
    
    promotions = query.all()
    
    # Filter by service_ids and min_amount
    applicable_promotions = []
    for promo in promotions:
        # Check service_ids
        if promo.service_ids:
            try:
                service_ids_list = json.loads(promo.service_ids)
                if service_id and service_id not in service_ids_list:
                    continue
            except (json.JSONDecodeError, TypeError):
                pass
        
        # Check min_amount
        if promo.min_amount and amount:
            if amount < promo.min_amount:
                continue
        
        applicable_promotions.append(promo)
    
    return applicable_promotions


def calculate_bonus_for_visit(
    db: Session,
    visit: Visit,
    program: LoyaltyProgram,
) -> Tuple[float, float, Optional[LoyaltyPromotion], Optional[LoyaltyLevel]]:
    """
    Calculate bonus points and cashback for a visit.
    
    Returns:
        (points_amount, cashback_amount, applied_promotion, client_level)
    """
    if not visit.price or visit.price <= 0:
        return 0.0, 0.0, None, None
    
    # Get client level
    client_level = None
    relation = crud_loyalty.get_client_loyalty_balance(db, visit.client_id, visit.business_id)
    if relation and relation.loyalty_level_id:
        client_level = crud_loyalty.get_loyalty_level(db, relation.loyalty_level_id)
    
    # Base multipliers
    points_multiplier = 1.0
    cashback_multiplier = 1.0
    
    if client_level:
        points_multiplier = client_level.points_multiplier or 1.0
        cashback_multiplier = client_level.cashback_multiplier or 1.0
    
    # Get applicable promotions
    promotions = get_active_promotions(
        db,
        program.id,
        client_id=visit.client_id,
        service_id=visit.service_id,
        amount=visit.price,
    )
    
    applied_promotion = None
    if promotions:
        # Use first applicable promotion (could be improved to use "best" promotion)
        applied_promotion = promotions[0]
        if applied_promotion.points_multiplier:
            points_multiplier *= applied_promotion.points_multiplier
        if applied_promotion.cashback_percent:
            cashback_multiplier *= (1.0 + applied_promotion.cashback_percent / 100.0)
    
    # Calculate base points
    if program.program_type in ('points', 'hybrid'):
        base_points = visit.price * program.points_per_currency
        points_amount = base_points * points_multiplier
        
        # Add bonus points from promotion
        if applied_promotion and applied_promotion.bonus_points:
            points_amount += applied_promotion.bonus_points
    else:
        points_amount = 0.0
    
    # Calculate cashback
    if program.program_type in ('cashback', 'hybrid'):
        if program.cashback_percent:
            base_cashback = visit.price * (program.cashback_percent / 100.0)
            cashback_amount = base_cashback * cashback_multiplier
        else:
            cashback_amount = 0.0
    else:
        cashback_amount = 0.0
    
    return points_amount, cashback_amount, applied_promotion, client_level


def award_bonus_for_visit(
    db: Session,
    visit: Visit,
    program: Optional[LoyaltyProgram] = None,
) -> Optional[LoyaltyTransaction]:
    """
    Award bonus points/cashback for a completed visit.
    Called automatically when visit status changes to 'completed'.
    """
    if not visit.business_id:
        return None
    
    # Get program
    if not program:
        program = crud_loyalty.get_loyalty_program(db, visit.business_id)
    
    if not program or not program.is_active:
        return None
    
    # Calculate bonus
    points_amount, cashback_amount, promotion, client_level = calculate_bonus_for_visit(
        db, visit, program
    )
    
    if points_amount <= 0 and (not cashback_amount or cashback_amount <= 0):
        return None
    
    # Calculate expiry date if applicable
    expiry_date = None
    if program.points_expiry_days:
        expiry_date = date.today() + timedelta(days=program.points_expiry_days)
    
    # Create transaction
    transaction = crud_loyalty.create_loyalty_transaction(
        db,
        client_id=visit.client_id,
        business_id=visit.business_id,
        transaction_type='earned',
        points_amount=points_amount,
        cashback_amount=cashback_amount if cashback_amount > 0 else None,
        program_id=program.id,
        visit_id=visit.id,
        promotion_id=promotion.id if promotion else None,
        level_id=client_level.id if client_level else None,
        description=f"Bonus for visit #{visit.id}",
        expiry_date=expiry_date,
    )
    
    logger.info(
        f"Awarded loyalty bonus: client={visit.client_id}, "
        f"points={points_amount}, cashback={cashback_amount}, visit={visit.id}"
    )
    
    # Update client level if needed
    if program.use_levels:
        update_client_level(db, visit.client_id, visit.business_id, program)
    
    return transaction


def award_birthday_bonus(
    db: Session,
    client_id: int,
    business_id: int,
    program: Optional[LoyaltyProgram] = None,
) -> Optional[LoyaltyTransaction]:
    """Award birthday bonus to client."""
    if not program:
        program = crud_loyalty.get_loyalty_program(db, business_id)
    
    if not program or not program.is_active or not program.birthday_bonus_enabled:
        return None
    
    # Get client
    client = db.query(Client).filter(Client.id == client_id).first()
    if not client or not client.birth_date:
        return None
    
    # Check if today is birthday (within 7 days to account for timezone)
    today = date.today()
    birthday_this_year = date(today.year, client.birth_date.month, client.birth_date.day)
    
    # Check if birthday is within last 7 days (to avoid multiple awards)
    days_since_birthday = (today - birthday_this_year).days
    if days_since_birthday < 0:
        days_since_birthday = (today - date(today.year - 1, client.birth_date.month, client.birth_date.day)).days
    
    if days_since_birthday > 7:
        return None
    
    # Check if already awarded this year
    relation = crud_loyalty.get_client_loyalty_balance(db, client_id, business_id)
    if relation:
        # Check recent transactions for birthday bonus
        recent_birthday_tx = db.query(LoyaltyTransaction).filter(
            LoyaltyTransaction.client_id == client_id,
            LoyaltyTransaction.business_id == business_id,
            LoyaltyTransaction.transaction_type == 'birthday_bonus',
            LoyaltyTransaction.created_at >= datetime(today.year, 1, 1),
        ).first()
        
        if recent_birthday_tx:
            return None  # Already awarded this year
    
    # Award bonus
    points_amount = program.birthday_bonus_points or 0.0
    cashback_amount = None
    
    if points_amount > 0 or program.birthday_bonus_percent:
        transaction = crud_loyalty.create_loyalty_transaction(
            db,
            client_id=client_id,
            business_id=business_id,
            transaction_type='birthday_bonus',
            points_amount=points_amount,
            cashback_amount=cashback_amount,
            program_id=program.id,
            description=f"Birthday bonus for {client.full_name}",
        )
        
        logger.info(f"Awarded birthday bonus: client={client_id}, points={points_amount}")
        return transaction
    
    return None


def update_client_level(
    db: Session,
    client_id: int,
    business_id: int,
    program: LoyaltyProgram,
) -> Optional[LoyaltyLevel]:
    """Update client loyalty level based on spending."""
    if not program.use_levels:
        return None
    
    # Get client relation and spending
    relation = crud_loyalty.get_client_loyalty_balance(db, client_id, business_id)
    if not relation:
        return None
    
    # Get all levels sorted by min_spending
    levels = crud_loyalty.list_loyalty_levels(db, program_id=program.id)
    levels_sorted = sorted(levels, key=lambda x: x.min_spending, reverse=True)
    
    # Find appropriate level
    new_level = None
    for level in levels_sorted:
        if relation.spending >= level.min_spending:
            new_level = level
            break
    
    # Update level if changed
    if new_level and relation.loyalty_level_id != new_level.id:
        old_level_id = relation.loyalty_level_id
        relation.loyalty_level_id = new_level.id
        db.commit()
        
        # Create transaction for level change
        if old_level_id:
            crud_loyalty.create_loyalty_transaction(
                db,
                client_id=client_id,
                business_id=business_id,
                transaction_type='level_up',
                points_amount=0.0,
                program_id=program.id,
                level_id=new_level.id,
                description=f"Level upgraded to {new_level.name}",
            )
        
        logger.info(f"Updated client level: client={client_id}, level={new_level.name}")
        return new_level
    
    return None


def use_points_for_visit(
    db: Session,
    client_id: int,
    business_id: int,
    points_to_use: float,
    cashback_to_use: float,
    visit_id: Optional[int] = None,
    visit_amount: Optional[float] = None,
    program: Optional[LoyaltyProgram] = None,
) -> Tuple[bool, float, str]:
    """
    Use loyalty points/cashback for payment.
    
    Returns:
        (success, discount_amount, error_message)
    """
    if not program:
        program = crud_loyalty.get_loyalty_program(db, business_id)
    
    if not program or not program.is_active:
        return False, 0.0, "Loyalty program not active"
    
    # Get client balance
    relation = crud_loyalty.get_client_loyalty_balance(db, client_id, business_id)
    if not relation:
        return False, 0.0, "Client not found"
    
    # Check balances
    if points_to_use > 0 and relation.loyalty_points < points_to_use:
        return False, 0.0, "Insufficient points"
    
    if cashback_to_use > 0 and relation.loyalty_cashback < cashback_to_use:
        return False, 0.0, "Insufficient cashback"
    
    # Calculate discount amount
    discount_amount = cashback_to_use  # Cashback is already in currency
    
    if points_to_use > 0:
        if not program.currency_per_point:
            return False, 0.0, "Points cannot be converted to currency"
        discount_amount += points_to_use * program.currency_per_point
    
    # Check max bonus payment percent
    if visit_amount and program.max_bonus_payment_percent:
        max_discount = visit_amount * (program.max_bonus_payment_percent / 100.0)
        if discount_amount > max_discount:
            discount_amount = max_discount
            # Adjust points/cashback proportionally
            if points_to_use > 0:
                points_to_use = (discount_amount - cashback_to_use) / program.currency_per_point
            if cashback_to_use > discount_amount:
                cashback_to_use = discount_amount
    
    # Create transactions (negative amounts)
    if points_to_use > 0:
        crud_loyalty.create_loyalty_transaction(
            db,
            client_id=client_id,
            business_id=business_id,
            transaction_type='spent',
            points_amount=-points_to_use,
            program_id=program.id,
            visit_id=visit_id,
            description=f"Used {points_to_use} points for payment",
        )
    
    if cashback_to_use > 0:
        crud_loyalty.create_loyalty_transaction(
            db,
            client_id=client_id,
            business_id=business_id,
            transaction_type='spent',
            cashback_amount=-cashback_to_use,
            program_id=program.id,
            visit_id=visit_id,
            description=f"Used {cashback_to_use} cashback for payment",
        )
    
    logger.info(
        f"Used loyalty bonus: client={client_id}, points={points_to_use}, "
        f"cashback={cashback_to_use}, discount={discount_amount}"
    )
    
    return True, discount_amount, ""
