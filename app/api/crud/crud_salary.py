from typing import Optional, List, Tuple
from datetime import datetime
from sqlalchemy.orm import Session
from sqlalchemy import and_, case, func, or_, select
from app.api.core.pagination import PaginationParams
from app.db.models.salary_calculation import SalaryCalculation
from app.db.models.salary_adjustment import SalaryAdjustment
from app.db.models.salary_payment import SalaryPayment
from app.db.models.employee import Employee
from app.db.models.visit import Visit
from app.db.models.visit_item import VisitItem
from app.api.crud import crud_employee
from app.schemas.salary import (
    SalaryCalculationUpdate,
    SalaryAdjustmentCreate,
    SalaryPaymentCreate,
    SalaryPaymentUpdate
)
from app.api.core.clock import utcnow
from decimal import Decimal, ROUND_HALF_UP, localcontext, Inexact, Rounded
from functools import wraps
import logging

logger = logging.getLogger(__name__)
from app.api.core.payroll_money import PayrollError, amount, rate, currency as valid_currency
from app.db.models.salary_period import SalaryPeriod


def _exact_context(fn):
    @wraps(fn)
    def call(*args, **kwargs):
        with localcontext() as ctx:
            ctx.prec = 80
            ctx.traps[Inexact] = False
            ctx.traps[Rounded] = False
            return fn(*args, **kwargs)
    return call


def _commission(revenue, percentage):
    revenue = amount(revenue)
    percentage = rate(percentage)
    with localcontext() as ctx:
        ctx.prec = max(80, len(revenue.as_tuple().digits) + len(percentage.as_tuple().digits) + 10)
        ctx.traps[Inexact] = False
        ctx.traps[Rounded] = False
        return (revenue * percentage / 100).quantize(Decimal('.01'), rounding=ROUND_HALF_UP)


def _mirror(calc, name, value):
    value = amount(value, signed=name == 'adjustments_total')
    setattr(calc, name + '_exact', value)
    setattr(calc, name, float(value))


def _lock_employee(db, employee_id):
    employee = db.query(Employee).filter(Employee.id == employee_id).populate_existing().with_for_update().first()
    if not employee:
        raise PayrollError('payroll_employee_missing')
    return employee


@_exact_context
def _lock_calc(db, calc_id, known=True):
    # All writers take employee first, then calculation, then child rows.
    employee_id = db.query(SalaryCalculation.employee_id).filter(SalaryCalculation.id == calc_id).scalar()
    if employee_id is None:
        raise PayrollError('payroll_calculation_missing')
    _lock_employee(db, employee_id)
    calc = db.query(SalaryCalculation).filter(SalaryCalculation.id == calc_id).populate_existing().with_for_update().one()
    if known:
        if calc.currency is None or calc.salary_period_id is None:
            raise PayrollError('payroll_legacy_review')
        _validate_calc(db, calc)
    return calc


@_exact_context
def _validate_calc(db, calc):
    valid_currency(calc.currency)
    period = db.query(SalaryPeriod).filter(SalaryPeriod.id == calc.salary_period_id).populate_existing().first()
    if period is None or (period.employee_id, period.business_id, period.period_start, period.period_end) != (
            calc.employee_id, calc.business_id_snapshot, calc.period_start, calc.period_end):
        raise PayrollError('payroll_snapshot_review')
    base = amount(period.base_salary_exact)
    if period.base_salary_currency is not None or base:
        valid_currency(period.base_salary_currency)
    expected_base = base if calc.currency == period.base_salary_currency else Decimal(0)
    expected_commission = _commission(calc.total_revenue_exact, period.commission_percentage)
    if amount(calc.base_salary_exact) != expected_base or amount(calc.commission_amount_exact) != expected_commission:
        raise PayrollError('payroll_snapshot_review')
    if amount(calc.total_salary_exact) != expected_base + expected_commission:
        raise PayrollError('payroll_snapshot_review')


def _completed(db, calc):
    return db.query(SalaryPayment).filter(SalaryPayment.salary_calculation_id == calc.id,
        SalaryPayment.status == 'completed').order_by(SalaryPayment.id).all()


@_exact_context
def _sync(db, calc):
    with db.no_autoflush:
        adjustments = db.query(SalaryAdjustment).filter(SalaryAdjustment.salary_calculation_id == calc.id).all()
        payments = _completed(db, calc)
    for child in [*adjustments, *payments]:
        if child.currency != calc.currency or child.amount_exact is None:
            raise PayrollError('payroll_legacy_review')
    total_adjustments = sum((amount(a.amount_exact, signed=True) for a in adjustments), Decimal(0))
    paid = sum((amount(p.amount_exact) for p in payments), Decimal(0))
    total = amount(calc.total_salary_exact) + total_adjustments
    if total < 0 or total < paid:
        raise PayrollError('payroll_total_below_paid')
    _mirror(calc, 'adjustments_total', total_adjustments)
    _mirror(calc, 'total_with_adjustments', total)
    _mirror(calc, 'paid_amount', paid)
    calc.payment_status = 'paid' if paid >= total else ('partial' if paid else 'unpaid')
    calc.is_paid = calc.payment_status == 'paid'
    calc.paid_at = (calc.paid_at or utcnow()) if calc.is_paid else None


@_exact_context
def calculate_salary(db: Session, employee_id: int, period_start: datetime,
                     period_end: datetime, calculated_by_id=None, notes=None, currency=None):
    employee = _lock_employee(db, employee_id)
    if not employee.is_active:
        raise PayrollError('payroll_employee_inactive')
    if period_start >= period_end:
        raise PayrollError('payroll_invalid_period')
    business_id = employee.business_owner.business_id
    if business_id is None:
        raise PayrollError('payroll_business_missing')
    overlaps = db.query(SalaryCalculation).filter(SalaryCalculation.employee_id == employee_id,
        SalaryCalculation.period_start < period_end, SalaryCalculation.period_end > period_start).all()
    for old in overlaps:
        if old.currency is None or old.salary_period_id is None:
            raise PayrollError('payroll_legacy_review')
        _validate_calc(db, old)
        if old.period_start != period_start or old.period_end != period_end:
            raise PayrollError('payroll_period_overlap')
    anchors = db.query(SalaryPeriod).filter(SalaryPeriod.employee_id == employee_id,
        SalaryPeriod.period_start < period_end, SalaryPeriod.period_end > period_start).all()
    period = None
    for anchor in anchors:
        if anchor.business_id != business_id or anchor.period_start != period_start or anchor.period_end != period_end:
            raise PayrollError('payroll_period_overlap')
        period = anchor
    if period is None and employee.base_salary_exact is None:
        raise PayrollError('payroll_legacy_review')
    base = amount(period.base_salary_exact if period else employee.base_salary_exact)
    base_currency = period.base_salary_currency if period else employee.base_salary_currency
    if base or base_currency is not None:
        valid_currency(base_currency)
    commission_rate = rate(period.commission_percentage if period else employee.commission_percentage or 0)
    # One statement provides a coherent quote even if a reprice commits immediately afterwards.
    rows = db.query(Visit.id.label('visit_id'), Visit.status, Visit.currency_snapshot,
        Visit.price.label('parent_price'), VisitItem.id.label('item_id'),
        VisitItem.specialist_id.label('item_specialist'), VisitItem.price.label('item_price')).outerjoin(
        VisitItem, VisitItem.visit_id == Visit.id).filter(
        Visit.business_id == business_id, Visit.deleted_at.is_(None),
        Visit.date_start >= period_start, Visit.date_start < period_end,
        or_(Visit.items.any(VisitItem.specialist_id == employee.employee_user_id),
            and_(~Visit.items.any(), Visit.specialist_id == employee.employee_user_id))).order_by(Visit.id, VisitItem.id).all()
    visits = {}
    for row in rows:
        entry = visits.setdefault(row.visit_id, {'status': row.status, 'currency': row.currency_snapshot, 'prices': []})
        if row.item_id is None:
            entry['prices'].append(row.parent_price)
        elif row.item_specialist == employee.employee_user_id:
            entry['prices'].append(row.item_price)
    gross = {}; invalid = []
    for visit_id, visit in visits.items():
        if visit['status'] != 'completed':
            continue
        try:
            unit = valid_currency(visit['currency'])
            value = sum((amount(price) for price in visit['prices']), Decimal(0))
            gross[unit] = gross.get(unit, Decimal(0)) + value
        except PayrollError:
            invalid.append(visit_id)
    if invalid:
        raise PayrollError('payroll_visit_review', visit_ids=invalid)
    units = set(gross)
    if base:
        units.add(base_currency)
    elif not units and base_currency is not None:
        # A saved zero salary still proves its unit when there are no service amounts.
        units.add(valid_currency(base_currency))
    if currency is None:
        if len(units) != 1:
            raise PayrollError('payroll_currency_required', currencies=sorted(units))
        currency = units.pop()
    valid_currency(currency)
    if period is None:
        period = SalaryPeriod(business_id=business_id, employee_id=employee_id,
            period_start=period_start, period_end=period_end, base_salary_currency=base_currency,
            base_salary_exact=base, commission_percentage=commission_rate, created_by_id=calculated_by_id)
        db.add(period); db.flush()
    calc = db.query(SalaryCalculation).filter(SalaryCalculation.salary_period_id == period.id,
        SalaryCalculation.currency == currency).populate_existing().with_for_update().first()
    if calc is not None and _completed(db, calc):
        raise PayrollError('payroll_correction_required')
    if calc is None:
        calc = SalaryCalculation(employee_id=employee_id, period_start=period_start, period_end=period_end,
            salary_period_id=period.id, currency=currency, business_id_snapshot=business_id,
            calculated_by_id=calculated_by_id)
        db.add(calc)
    selected = [v for v in visits.values() if v['currency'] == currency]
    calc.total_visits = len(selected)
    calc.completed_visits = sum(v['status'] == 'completed' for v in selected)
    calc.cancelled_visits = sum(v['status'] == 'cancelled' for v in selected)
    revenue = amount(gross.get(currency, Decimal(0)))
    commission = _commission(revenue, commission_rate)
    _mirror(calc, 'total_revenue', revenue)
    _mirror(calc, 'commission_amount', commission)
    _mirror(calc, 'base_salary', base if currency == base_currency else Decimal(0))
    _mirror(calc, 'total_salary', calc.base_salary_exact + commission)
    if calc.id is None:
        _mirror(calc, 'adjustments_total', Decimal(0)); _mirror(calc, 'paid_amount', Decimal(0))
        _mirror(calc, 'total_with_adjustments', calc.total_salary_exact)
        calc.payment_status = 'unpaid'; calc.is_paid = False
    else:
        _sync(db, calc)
    calc.notes = notes
    db.commit(); db.refresh(calc)
    return calc
def get_salary_calculation(
    db: Session,
    salary_id: int,
    employee_id: Optional[int] = None
) -> Optional[SalaryCalculation]:
    query = db.query(SalaryCalculation).filter(SalaryCalculation.id == salary_id)
    if employee_id is not None:
        query = query.filter(SalaryCalculation.employee_id == employee_id)
    return query.options(
    ).first()
def list_salary_calculations(
    db: Session,
    employee_id: Optional[int] = None,
    is_paid: Optional[bool] = None,
    payment_status: Optional[str] = None,
    pagination: Optional[PaginationParams] = None
) -> Tuple[List[SalaryCalculation], int]:
    query = db.query(SalaryCalculation)
    if employee_id is not None:
        query = query.filter(SalaryCalculation.employee_id == employee_id)
    if is_paid is not None:
        query = query.filter(SalaryCalculation.is_paid == is_paid)
    if payment_status is not None:
        query = query.filter(SalaryCalculation.payment_status == payment_status)
    query = query.order_by(SalaryCalculation.calculated_at.desc())
    total = query.count()
    if pagination:
        query = query.offset(pagination.offset).limit(pagination.limit)
    items = query.all()
    return items, total
@_exact_context
def update_salary_calculation(db, salary_calculation, update):
    calc = _lock_calc(db, salary_calculation.id, known=False)
    data = update.model_dump(exclude_unset=True)
    if 'is_paid' in data and data['is_paid'] != calc.is_paid:
        raise PayrollError('payroll_legacy_review' if calc.currency is None else 'payroll_payment_required')
    if 'notes' in data:
        calc.notes = data['notes']
    db.commit(); db.refresh(calc)
    return calc


def _update_salary_payment_status(db, salary_calculation):
    _sync(db, salary_calculation)


@_exact_context
def create_salary_adjustment(db, salary_calculation_id, adjustment_in, created_by_id):
    calc = _lock_calc(db, salary_calculation_id)
    unit = getattr(adjustment_in, 'currency', None)
    if unit is not None and unit != calc.currency:
        raise PayrollError('payroll_currency_mismatch')
    value = amount(adjustment_in.amount, signed=True)
    row = SalaryAdjustment(salary_calculation_id=calc.id, type=adjustment_in.type,
        amount=float(value), amount_exact=value, currency=calc.currency,
        description=adjustment_in.description, created_by_id=created_by_id)
    db.add(row); db.flush(); _sync(db, calc)
    db.commit(); db.refresh(row)
    return row

def get_salary_adjustment(
    db: Session,
    adjustment_id: int,
    salary_calculation_id: Optional[int] = None
) -> Optional[SalaryAdjustment]:
    query = db.query(SalaryAdjustment).filter(SalaryAdjustment.id == adjustment_id)
    if salary_calculation_id is not None:
        query = query.filter(SalaryAdjustment.salary_calculation_id == salary_calculation_id)
    return query.first()
def list_salary_adjustments(
    db: Session,
    salary_calculation_id: int,
    adjustment_type: Optional[str] = None
) -> List[SalaryAdjustment]:
    query = db.query(SalaryAdjustment).filter(
        SalaryAdjustment.salary_calculation_id == salary_calculation_id
    )
    if adjustment_type:
        query = query.filter(SalaryAdjustment.type == adjustment_type)
    return query.order_by(SalaryAdjustment.created_at.desc()).all()
@_exact_context
def delete_salary_adjustment(db, adjustment_id, salary_calculation_id=None):
    row = get_salary_adjustment(db, adjustment_id, salary_calculation_id)
    if row is None:
        return False
    calc = _lock_calc(db, row.salary_calculation_id)
    if _completed(db, calc):
        raise PayrollError('payroll_correction_required')
    db.delete(row); db.flush(); _sync(db, calc); db.commit()
    return True


def _expense(db, calc, payment):
    from app.api.crud import crud_expense
    from app.db.models.expense_category import ExpenseCategory
    from app.schemas.expense import ExpenseCreate
    category = db.query(ExpenseCategory).filter(ExpenseCategory.is_system.is_(True),
        ExpenseCategory.code == 'salaries').first()
    expense = ExpenseCreate(business_id=calc.business_id_snapshot,
        category_id=category.id if category else None, amount=payment.amount_exact,
        currency=calc.currency, description=payment.notes, expense_date=payment.payment_date,
        payment_method=payment.payment_method or 'other')
    crud_expense.create_expense(db, expense, created_by_id=payment.created_by_id,
        source='salary_payment', employee_id=calc.employee_id, salary_payment_id=payment.id, commit=False)


@_exact_context
def _check_payment(db, calc, payment):
    if payment.currency != calc.currency:
        raise PayrollError('payroll_currency_mismatch')
    value = amount(payment.amount_exact)
    if value <= 0:
        raise PayrollError('payroll_invalid_amount')
    _sync(db, calc)
    if value > calc.total_with_adjustments_exact - calc.paid_amount_exact:
        raise PayrollError('payroll_payment_exceeds_remaining')


@_exact_context
def create_salary_payment(db, salary_calculation_id, payment_in, created_by_id, business_id=None):
    try:
        calc = _lock_calc(db, salary_calculation_id)
        if business_id is not None and business_id != calc.business_id_snapshot:
            raise PayrollError('payroll_business_mismatch')
        unit = getattr(payment_in, 'currency', None)
        if unit is not None and unit != calc.currency:
            raise PayrollError('payroll_currency_mismatch')
        value = amount(payment_in.amount)
        payment = SalaryPayment(salary_calculation_id=calc.id, amount=float(value), amount_exact=value,
            currency=calc.currency, payment_date=payment_in.payment_date,
            payment_method=payment_in.payment_method, status=payment_in.status,
            notes=payment_in.notes, created_by_id=created_by_id)
        _check_payment(db, calc, payment)
        db.add(payment); db.flush()
        if payment.status == 'completed':
            _expense(db, calc, payment)
        _sync(db, calc)
        db.commit(); db.refresh(payment)
        return payment
    except PayrollError:
        db.rollback()
        raise
    except Exception as exc:
        logger.error('Payroll transaction failed (%s)', type(exc).__name__)
        db.rollback()
        raise PayrollError('payroll_transaction_failed') from None

def get_salary_payment(
    db: Session,
    payment_id: int,
    salary_calculation_id: Optional[int] = None
) -> Optional[SalaryPayment]:
    query = db.query(SalaryPayment).filter(SalaryPayment.id == payment_id)
    if salary_calculation_id is not None:
        query = query.filter(SalaryPayment.salary_calculation_id == salary_calculation_id)
    return query.first()
def list_salary_payments(
    db: Session,
    salary_calculation_id: int,
    status: Optional[str] = None
) -> List[SalaryPayment]:
    query = db.query(SalaryPayment).filter(
        SalaryPayment.salary_calculation_id == salary_calculation_id
    )
    if status:
        query = query.filter(SalaryPayment.status == status)
    return query.order_by(SalaryPayment.payment_date.desc()).all()
@_exact_context
def update_salary_payment(db, payment, update):
    try:
        calc = _lock_calc(db, payment.salary_calculation_id)
        payment = db.query(SalaryPayment).filter(SalaryPayment.id == payment.id).populate_existing().with_for_update().one()
        data = update.model_dump(exclude_unset=True)
        if payment.status == 'completed':
            raise PayrollError('payroll_correction_required')
        if payment.currency != calc.currency or payment.amount_exact is None:
            raise PayrollError('payroll_legacy_review')
        if data.get('currency', calc.currency) != calc.currency:
            raise PayrollError('payroll_currency_mismatch')
        if 'amount' in data:
            value = amount(data.pop('amount'))
            payment.amount = float(value); payment.amount_exact = value
        target_status = data.pop('status', payment.status)
        data.pop('currency', None)
        for key, value in data.items():
            setattr(payment, key, value)
        _check_payment(db, calc, payment)
        payment.status = target_status
        db.flush()
        if payment.status == 'completed':
            _expense(db, calc, payment)
        _sync(db, calc); db.commit(); db.refresh(payment)
        return payment
    except PayrollError:
        db.rollback()
        raise
    except Exception as exc:
        logger.error('Payroll transaction failed (%s)', type(exc).__name__)
        db.rollback()
        raise PayrollError('payroll_transaction_failed') from None


@_exact_context
def delete_salary_payment(db, payment_id, salary_calculation_id=None):
    payment = get_salary_payment(db, payment_id, salary_calculation_id)
    if payment is None:
        return False
    calc = _lock_calc(db, payment.salary_calculation_id)
    payment = db.query(SalaryPayment).filter(SalaryPayment.id == payment.id).populate_existing().with_for_update().one()
    if payment.status == 'completed':
        raise PayrollError('payroll_correction_required')
    if payment.currency != calc.currency or payment.amount_exact is None:
        raise PayrollError('payroll_legacy_review')
    db.delete(payment); db.flush(); _sync(db, calc); db.commit()
    return True

def get_current_unpaid_salary(
    db: Session,
    employee_user_id: Optional[int] = None,
    employee_id: Optional[int] = None,
    currency: Optional[str] = None
) -> Optional[SalaryCalculation]:
    if employee_id is not None:
        target_employee_id = employee_id
    elif employee_user_id is not None:
        employee = crud_employee.get_employee_by_user_id(db, employee_user_id)
        if not employee:
            return None
        target_employee_id = employee.id
    else:
        return None
    query = db.query(SalaryCalculation).filter(
        and_(
            SalaryCalculation.employee_id == target_employee_id,
            SalaryCalculation.payment_status.in_(['unpaid', 'partial'])
        )
    )
    if currency is not None:
        query = query.filter(SalaryCalculation.currency == valid_currency(currency))
    units = {row[0] for row in query.with_entities(SalaryCalculation.currency).distinct().all()}
    if currency is None and len(units) > 1:
        raise PayrollError('payroll_currency_required', currencies=sorted(str(unit) for unit in units))
    return query.order_by(SalaryCalculation.calculated_at.desc()).first()
def get_salary_history(
    db: Session,
    employee_user_id: int,
    limit: int = 10
) -> List[SalaryCalculation]:
    employee = crud_employee.get_employee_by_user_id(db, employee_user_id)
    if not employee:
        return []
    return db.query(SalaryCalculation).filter(
        SalaryCalculation.employee_id == employee.id
    ).order_by(SalaryCalculation.calculated_at.desc()).limit(limit).all()
