import calendar
from datetime import date, datetime, timedelta
from typing import Optional, Tuple, List
from sqlalchemy import or_, Numeric, cast, select
from sqlalchemy.dialects.postgresql import insert
from decimal import Decimal, localcontext, ROUND_HALF_UP, ROUND_DOWN, InvalidOperation
from math import nextafter
from app.api.core import cashback_wallets
from app.db.models.business import Business
from app.db.models.client_business_relation import ClientBusinessRelation
from app.schemas.limits import ISO_4217
from sqlalchemy.orm import Session
from app.db.models.loyalty_program import (
    LoyaltyProgram,
    LoyaltyLevel,
    LoyaltyPromotion,
    LoyaltyTransaction,
)
from app.db.models.visit import Visit
from app.db.models.client import Client
from app.api.crud import crud_loyalty
from app.api.core.logger import logger
import json
def _positive_money(value):
    try:
        amount = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return amount if amount.is_finite() and amount > 0 else None


def _add_turnover(left, right):
    whole = max(max(0, len(value.as_tuple().digits) + value.as_tuple().exponent)
                for value in (left, right))
    fraction = max(max(0, -value.as_tuple().exponent) for value in (left, right))
    with localcontext() as context:
        context.prec = max(1, whole + fraction + 1)
        return left + right


def _current_client_level(db, client_id, business_id, program):
    """Prove eligibility from this program's known-unit turnover, not a cached level id."""
    if not program.use_levels or program.business_id != business_id:
        return None
    with db.no_autoflush:
        # A clean identity map keeps stale or pending caller models out of the proof.
        with Session(bind=db.connection(), autoflush=False, join_transaction_mode='rollback_only') as reads:
            currency = reads.execute(select(Business.currency).where(Business.id == business_id)).scalar_one_or_none()
            if currency not in ISO_4217:
                return None
            prices = reads.execute(select(cast(Visit.price, Numeric())).where(
                Visit.business_id == business_id, Visit.client_id == client_id,
                Visit.status == 'completed', Visit.deleted_at.is_(None),
                Visit.currency_snapshot == currency, Visit.price > 0,
                Visit.price < float('inf'))).scalars().all()
            turnover = Decimal('0')
            for price in prices:
                amount = _positive_money(price)
                if amount is not None:
                    turnover = _add_turnover(turnover, amount)
            candidates = []
            for level in reads.query(LoyaltyLevel).filter(LoyaltyLevel.program_id == program.id).all():
                minimum = Decimal(str(level.min_spending))
                if minimum.is_finite() and minimum >= 0 and turnover >= minimum:
                    candidates.append((minimum, level.level_number, level.id, level))
            return max(candidates, key=lambda item: item[:3])[3] if candidates else None


def _stage_client_level(db, client_id, business_id, level):
    """Stage the level with the award, using the same relation anchor as the wallet writer."""
    with db.no_autoflush:
        relation = db.query(ClientBusinessRelation).filter_by(client_id=client_id, business_id=business_id).with_for_update().one_or_none()
        if relation is None:
            db.execute(insert(ClientBusinessRelation).values(client_id=client_id, business_id=business_id,
                loyalty_points=0.0, loyalty_cashback=0.0).on_conflict_do_nothing(index_elements=['client_id', 'business_id']))
            relation = db.query(ClientBusinessRelation).filter_by(client_id=client_id, business_id=business_id).with_for_update().one()
        relation.loyalty_level_id = level.id if level is not None else None
        return relation


def get_active_promotions(
    db: Session,
    program_id: int,
    client_id: Optional[int] = None,
    service_id: Optional[int] = None,
    amount: Optional[float] = None,
    amount_currency: Optional[str] = None,
    program_currency: Optional[str] = None,
) -> List[LoyaltyPromotion]:
    today = date.today()
    with db.no_autoflush:
        with Session(bind=db.connection(), autoflush=False, join_transaction_mode='rollback_only') as reads:
            query = reads.query(LoyaltyPromotion).filter(
                LoyaltyPromotion.program_id == program_id,
                LoyaltyPromotion.is_active == True,
                LoyaltyPromotion.start_date <= today,
                LoyaltyPromotion.end_date >= today,
            )
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
    applicable_promotions = []
    for promo in promotions:
        if promo.service_ids:
            try:
                service_ids_list = json.loads(promo.service_ids)
                if service_id and service_id not in service_ids_list:
                    continue
            except (json.JSONDecodeError, TypeError):
                pass
        if promo.min_amount is not None:
            if amount_currency not in ISO_4217 or amount_currency != program_currency:
                continue
            minimum = Decimal(str(promo.min_amount))
            value = _positive_money(amount)
            if not minimum.is_finite() or value is None or value < minimum:
                continue
        applicable_promotions.append(promo)
    return applicable_promotions
def calculate_bonus_for_visit(
    db: Session,
    visit: Visit,
    program: LoyaltyProgram,
) -> Tuple[float, float, Optional[LoyaltyPromotion], Optional[LoyaltyLevel]]:
    if not visit.price or visit.price <= 0:
        return 0.0, 0.0, None, None
    client_level = _current_client_level(db, visit.client_id, visit.business_id, program)
    with db.no_autoflush:
        program_currency = db.execute(select(Business.currency).where(Business.id == visit.business_id)).scalar_one_or_none()
    points_multiplier = 1.0
    cashback_multiplier = 1.0
    if client_level:
        points_multiplier = client_level.points_multiplier if client_level.points_multiplier is not None else 1.0
        cashback_multiplier = client_level.cashback_multiplier if client_level.cashback_multiplier is not None else 1.0
    promotions = get_active_promotions(
        db,
        program.id,
        client_id=visit.client_id,
        service_id=visit.service_id,
        amount=visit.price,
        amount_currency=visit.currency_snapshot,
        program_currency=program_currency,
    )
    applied_promotion = None
    if promotions:
        applied_promotion = promotions[0]
        if applied_promotion.points_multiplier is not None:
            points_multiplier *= applied_promotion.points_multiplier
        if applied_promotion.cashback_percent:
            cashback_multiplier *= (1.0 + applied_promotion.cashback_percent / 100.0)
    if program.program_type in ('points', 'hybrid'):
        base_points = visit.price * program.points_per_currency
        points_amount = base_points * points_multiplier
        if applied_promotion and applied_promotion.bonus_points:
            points_amount += applied_promotion.bonus_points
    else:
        points_amount = 0.0
    if program.program_type in ('cashback', 'hybrid'):
        if program.cashback_percent:
            with localcontext() as money_context:
                money_context.prec = 40
                multiplier = Decimal(str(client_level.cashback_multiplier if client_level.cashback_multiplier is not None else 1)) if client_level else Decimal(1)
                if applied_promotion and applied_promotion.cashback_percent:
                    multiplier *= 1 + Decimal(str(applied_promotion.cashback_percent)) / 100
                cashback_amount = (Decimal(str(visit.price)) * Decimal(str(program.cashback_percent))
                                   / 100 * multiplier).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
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
    if not visit.business_id:
        return None
    visit = crud_loyalty.lock_loyalty_visit(db, visit.id, visit.client_id, visit.business_id)
    # Reopening and completing the same visit must not credit it a second time.
    previous = db.query(LoyaltyTransaction).filter(
        LoyaltyTransaction.visit_id == visit.id, LoyaltyTransaction.business_id == visit.business_id,
        LoyaltyTransaction.client_id == visit.client_id, LoyaltyTransaction.transaction_type == 'earned',
        or_(LoyaltyTransaction.points_amount != 0, LoyaltyTransaction.cashback_amount != 0)
    ).order_by(LoyaltyTransaction.id).first()
    if previous is not None:
        return previous
    program = crud_loyalty.lock_loyalty_program(db, visit.business_id, program.id if program is not None else None)
    if not program or not program.is_active:
        return None
    unit = visit.currency_snapshot
    if visit.price and visit.price > 0 and program.program_type in ('points', 'hybrid'):
        business_unit = db.query(Business.currency).filter(Business.id == visit.business_id).scalar()
        if unit is None or unit != business_unit:
            raise cashback_wallets.CashbackError('loyalty_points_currency_unavailable')
    points_amount, cashback_amount, promotion, client_level = calculate_bonus_for_visit(
        db, visit, program
    )
    if points_amount <= 0 and (not cashback_amount or cashback_amount <= 0):
        return None
    expiry_date = None
    if program.points_expiry_days:
        expiry_date = date.today() + timedelta(days=program.points_expiry_days)
    if program.use_levels:
        _stage_client_level(db, visit.client_id, visit.business_id, client_level)
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
    return transaction
def _birthday_in(year: int, birth_date: date) -> date:
    # 29 Feb in a non-leap year is celebrated on the 28th (date() would just blow up)
    if birth_date.month == 2 and birth_date.day == 29 and not calendar.isleap(year):
        return date(year, 2, 28)
    return date(year, birth_date.month, birth_date.day)
def award_birthday_bonus(
    db: Session,
    client_id: int,
    business_id: int,
    program: Optional[LoyaltyProgram] = None,
) -> Optional[LoyaltyTransaction]:
    if not program:
        program = crud_loyalty.get_loyalty_program(db, business_id)
    if not program or not program.is_active or not program.birthday_bonus_enabled:
        return None
    client = db.query(Client).filter(Client.id == client_id).first()
    if not client or not client.birth_date:
        return None
    today = date.today()
    birthday_this_year = _birthday_in(today.year, client.birth_date)
    days_since_birthday = (today - birthday_this_year).days
    if days_since_birthday < 0:
        days_since_birthday = (today - _birthday_in(today.year - 1, client.birth_date)).days
    if days_since_birthday > 7:
        return None
    relation = crud_loyalty.get_client_loyalty_balance(db, client_id, business_id)
    if relation:
        recent_birthday_tx = db.query(LoyaltyTransaction).filter(
            LoyaltyTransaction.client_id == client_id,
            LoyaltyTransaction.business_id == business_id,
            LoyaltyTransaction.transaction_type == 'birthday_bonus',
            LoyaltyTransaction.created_at >= datetime(today.year, 1, 1),
        ).first()
        if recent_birthday_tx:
            return None  
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
    cashback_wallets.lock_money_references(db, client_id, business_id)
    program = crud_loyalty.lock_loyalty_program(db, business_id, program.id)
    if program is None or not program.use_levels:
        return None
    level = _current_client_level(db, client_id, business_id, program)
    with db.no_autoflush:
        relation = db.query(ClientBusinessRelation).filter_by(client_id=client_id, business_id=business_id).with_for_update().one_or_none()
        if relation is None:
            return None
        old_level_id = relation.loyalty_level_id
        new_level_id = level.id if level else None
        if old_level_id == new_level_id:
            return level
        relation.loyalty_level_id = new_level_id
    if old_level_id is not None and level is not None:
        crud_loyalty.create_loyalty_transaction(db, client_id=client_id, business_id=business_id,
            transaction_type='level_up', points_amount=0.0, program_id=program.id,
            level_id=level.id, description=f'Level changed to {level.name}')
    else:
        db.commit()
    return level


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
    if visit_id is None:
        raise cashback_wallets.CashbackError('loyalty_cashback_visit_required')
    visit = crud_loyalty.lock_loyalty_visit(db, visit_id, client_id, business_id)
    if db.query(LoyaltyTransaction.id).filter(
        LoyaltyTransaction.visit_id == visit.id, LoyaltyTransaction.business_id == business_id,
        LoyaltyTransaction.client_id == client_id, LoyaltyTransaction.transaction_type == 'spent',
        or_(LoyaltyTransaction.points_amount != 0, LoyaltyTransaction.cashback_amount != 0)
    ).first() is not None:
        return False, 0.0, 'loyalty_payment_already_recorded'
    from app.api.core.loyalty_settlement_guard import ensure_spend_allowed
    ensure_spend_allowed(db, visit.id, business_id)
    unit = visit.currency_snapshot
    if points_to_use > 0:
        business_unit = db.query(Business.currency).filter(Business.id == business_id).scalar()
        if unit is None or unit != business_unit:
            raise cashback_wallets.CashbackError('loyalty_points_currency_unavailable')
    program = crud_loyalty.lock_loyalty_program(db, business_id, program.id if program is not None else None)
    if not program or not program.is_active:
        return False, 0.0, "Loyalty program not active"
    relation = crud_loyalty.get_client_loyalty_balance(db, client_id, business_id)
    if not relation:
        return False, 0.0, "Client not found"
    try:
        requested_points = Decimal(str(points_to_use))
        requested_cash = cashback_wallets._amount(cashback_to_use)
        quote = Decimal(str(visit.price))
        percent = Decimal(str(program.max_bonus_payment_percent if program.max_bonus_payment_percent is not None else 100))
        rate = Decimal(str(program.currency_per_point or 0))
        if (not requested_points.is_finite() or requested_points < 0 or requested_cash < 0
                or not quote.is_finite() or quote <= 0 or not percent.is_finite() or not 0 <= percent <= 100
                or not rate.is_finite() or rate < 0):
            return False, 0.0, 'loyalty_payment_amount_unavailable'
    except (InvalidOperation, ValueError):
        return False, 0.0, 'loyalty_payment_amount_unavailable'
    if requested_points and rate <= 0:
        return False, 0.0, "Points cannot be converted to currency"
    with localcontext() as money_context:
        money_context.prec = 40
        money_context.rounding = ROUND_DOWN
        cap = (quote * percent / 100).quantize(Decimal('0.01'), rounding=ROUND_DOWN)
        cash = min(requested_cash, cap)
        points_money = min(requested_points * rate, cap - cash).quantize(Decimal('0.01'), rounding=ROUND_DOWN)
        points = float(points_money / rate) if points_money and rate else 0.0
        if points and Decimal(str(points)) * rate > points_money:
            points = nextafter(points, 0.0)
        discount = cash + points_money
    if discount <= 0:
        return False, 0.0, 'loyalty_payment_amount_unavailable'
    if relation.loyalty_points < points:
        return False, 0.0, 'loyalty_balance_insufficient'
    if cash and cashback_wallets.known_balance(db, client_id, business_id, unit) < cash:
        return False, 0.0, 'loyalty_balance_insufficient'
    crud_loyalty.create_loyalty_transaction(
        db, client_id=client_id, business_id=business_id, transaction_type='spent',
        points_amount=-points if points else 0.0,
        cashback_amount=cash.copy_negate() if cash else 0.0,
        program_id=program.id, visit_id=visit_id, description='Used loyalty balance for payment',
    )
    logger.info(f"Used loyalty bonus: client={client_id}, points={points}, cashback={cash}, discount={discount}")
    return True, float(discount), ""
