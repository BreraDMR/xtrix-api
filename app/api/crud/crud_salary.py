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
def calculate_salary(
    db: Session,
    employee_id: int,
    period_start: datetime,
    period_end: datetime,
    calculated_by_id: Optional[int] = None,
    notes: Optional[str] = None
) -> SalaryCalculation:
    employee = db.query(Employee).filter(Employee.id == employee_id).first()
    if not employee:
        raise ValueError(f"Employee with id={employee_id} not found")
    if not employee.is_active:
        raise ValueError(f"Employee with id={employee_id} is not active")
    # Each master earns from their own items; the visit still counts once per master.
    own_items = select(func.sum(VisitItem.price)).where(
        VisitItem.visit_id == Visit.id,
        VisitItem.specialist_id == employee.employee_user_id,
    ).correlate(Visit).scalar_subquery()
    legacy = ~Visit.items.any()
    visits_query = db.query(Visit).filter(
        Visit.deleted_at.is_(None),
        Visit.date_start >= period_start,
        Visit.date_start < period_end,
        or_(
            Visit.items.any(VisitItem.specialist_id == employee.employee_user_id),
            and_(legacy, Visit.specialist_id == employee.employee_user_id),
        ),
    )
    total_visits = visits_query.count()
    completed_visits = visits_query.filter(Visit.status == 'completed').count()
    cancelled_visits = visits_query.filter(Visit.status == 'cancelled').count()
    revenue_result = visits_query.filter(Visit.status == 'completed').with_entities(
        func.sum(case((legacy, Visit.price), else_=own_items))
    ).scalar()
    total_revenue = float(revenue_result) if revenue_result else 0.0
    commission_percentage = employee.commission_percentage or 0.0
    base_salary = employee.base_salary or 0.0
    commission_amount = (total_revenue * commission_percentage) / 100.0 if commission_percentage > 0 else 0.0
    total_salary = base_salary + commission_amount
    salary_calculation = SalaryCalculation(
        employee_id=employee_id,
        period_start=period_start,
        period_end=period_end,
        total_visits=total_visits,
        completed_visits=completed_visits,
        cancelled_visits=cancelled_visits,
        total_revenue=total_revenue,
        commission_amount=commission_amount,
        base_salary=base_salary,
        total_salary=total_salary,
        adjustments_total=0.0,
        total_with_adjustments=total_salary,
        paid_amount=0.0,
        payment_status='unpaid',
        is_paid=False,
        notes=notes,
        calculated_by_id=calculated_by_id
    )
    db.add(salary_calculation)
    db.commit()
    db.refresh(salary_calculation)
    return salary_calculation
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
def update_salary_calculation(
    db: Session,
    salary_calculation: SalaryCalculation,
    update: SalaryCalculationUpdate
) -> SalaryCalculation:
    update_data = update.model_dump(exclude_unset=True)
    if update_data.get('is_paid') is True and not salary_calculation.is_paid:
        update_data['paid_at'] = utcnow()
        update_data['payment_status'] = 'paid'
        update_data['paid_amount'] = salary_calculation.total_with_adjustments
    elif update_data.get('is_paid') is False:
        update_data['paid_at'] = None
        update_data['payment_status'] = 'unpaid'
        update_data['paid_amount'] = 0.0
    for field, value in update_data.items():
        setattr(salary_calculation, field, value)
    salary_calculation.updated_at = utcnow()
    db.add(salary_calculation)
    db.commit()
    db.refresh(salary_calculation)
    return salary_calculation
def _update_salary_payment_status(db: Session, salary_calculation: SalaryCalculation):
    total_paid = db.query(func.sum(SalaryPayment.amount)).filter(
        and_(
            SalaryPayment.salary_calculation_id == salary_calculation.id,
            SalaryPayment.status == 'completed'
        )
    ).scalar() or 0.0
    salary_calculation.paid_amount = float(total_paid)
    if salary_calculation.paid_amount >= salary_calculation.total_with_adjustments:
        salary_calculation.payment_status = 'paid'
        salary_calculation.is_paid = True
        if not salary_calculation.paid_at:
            salary_calculation.paid_at = utcnow()
    elif salary_calculation.paid_amount > 0:
        salary_calculation.payment_status = 'partial'
        salary_calculation.is_paid = False
    else:
        salary_calculation.payment_status = 'unpaid'
        salary_calculation.is_paid = False
    db.commit()
    db.refresh(salary_calculation)
def create_salary_adjustment(
    db: Session,
    salary_calculation_id: int,
    adjustment_in: SalaryAdjustmentCreate,
    created_by_id: int
) -> SalaryAdjustment:
    adjustment = SalaryAdjustment(
        salary_calculation_id=salary_calculation_id,
        type=adjustment_in.type,
        amount=adjustment_in.amount,
        description=adjustment_in.description,
        created_by_id=created_by_id
    )
    db.add(adjustment)
    db.flush()
    salary_calculation = get_salary_calculation(db, salary_calculation_id)
    if salary_calculation:
        adjustments_total = db.query(func.sum(SalaryAdjustment.amount)).filter(
            SalaryAdjustment.salary_calculation_id == salary_calculation_id
        ).scalar() or 0.0
        salary_calculation.adjustments_total = float(adjustments_total)
        salary_calculation.total_with_adjustments = salary_calculation.total_salary + salary_calculation.adjustments_total
        db.commit()
        db.refresh(salary_calculation)
    db.commit()
    db.refresh(adjustment)
    return adjustment
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
def delete_salary_adjustment(
    db: Session,
    adjustment_id: int,
    salary_calculation_id: Optional[int] = None
) -> bool:
    adjustment = get_salary_adjustment(db, adjustment_id, salary_calculation_id)
    if not adjustment:
        return False
    calc_id = adjustment.salary_calculation_id
    db.delete(adjustment)
    db.flush()
    salary_calculation = get_salary_calculation(db, calc_id)
    if salary_calculation:
        adjustments_total = db.query(func.sum(SalaryAdjustment.amount)).filter(
            SalaryAdjustment.salary_calculation_id == calc_id
        ).scalar() or 0.0
        salary_calculation.adjustments_total = float(adjustments_total)
        salary_calculation.total_with_adjustments = salary_calculation.total_salary + salary_calculation.adjustments_total
        db.commit()
    db.commit()
    return True
def create_salary_payment(
    db: Session,
    salary_calculation_id: int,
    payment_in: SalaryPaymentCreate,
    created_by_id: int,
    business_id: Optional[int] = None
) -> SalaryPayment:
    payment = SalaryPayment(
        salary_calculation_id=salary_calculation_id,
        amount=payment_in.amount,
        payment_date=payment_in.payment_date,
        payment_method=payment_in.payment_method,
        status=payment_in.status,
        notes=payment_in.notes,
        created_by_id=created_by_id
    )
    db.add(payment)
    db.flush()
    salary_calculation = get_salary_calculation(db, salary_calculation_id)
    if salary_calculation:
        _update_salary_payment_status(db, salary_calculation)
    if payment_in.status == 'completed' and business_id:
        from app.api.crud import crud_expense
        from app.db.models.expense_category import ExpenseCategory
        from app.schemas.expense import ExpenseCreate
        # the system "salaries" category; the frontend shows the text, we only store codes
        salary_category = db.query(ExpenseCategory).filter(
            ExpenseCategory.is_system.is_(True),
            ExpenseCategory.code == "salaries",
        ).first()
        expense = ExpenseCreate(
            business_id=business_id,
            category_id=salary_category.id if salary_category else None,
            amount=payment_in.amount,
            currency="EUR",  
            description=payment_in.notes or None,
            expense_date=payment_in.payment_date,
            payment_method=payment_in.payment_method or "other"
        )
        try:
            crud_expense.create_expense(
                db, expense, created_by_id=created_by_id,
                source="salary_payment",
                employee_id=salary_calculation.employee_id if salary_calculation else None,
            )
        except Exception as e:
            import logging
            logger = logging.getLogger(__name__)
            logger.error(f"Failed to create expense for salary payment: {e}")
    db.commit()
    db.refresh(payment)
    return payment
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
def update_salary_payment(
    db: Session,
    payment: SalaryPayment,
    update: SalaryPaymentUpdate
) -> SalaryPayment:
    update_data = update.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(payment, field, value)
    payment.updated_at = utcnow()
    db.commit()
    db.refresh(payment)
    salary_calculation = get_salary_calculation(db, payment.salary_calculation_id)
    if salary_calculation:
        _update_salary_payment_status(db, salary_calculation)
    return payment
def delete_salary_payment(
    db: Session,
    payment_id: int,
    salary_calculation_id: Optional[int] = None
) -> bool:
    payment = get_salary_payment(db, payment_id, salary_calculation_id)
    if not payment:
        return False
    calc_id = payment.salary_calculation_id
    db.delete(payment)
    db.flush()
    salary_calculation = get_salary_calculation(db, calc_id)
    if salary_calculation:
        _update_salary_payment_status(db, salary_calculation)
    db.commit()
    return True
def get_current_unpaid_salary(
    db: Session,
    employee_user_id: Optional[int] = None,
    employee_id: Optional[int] = None
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
    unpaid_salary = db.query(SalaryCalculation).filter(
        and_(
            SalaryCalculation.employee_id == target_employee_id,
            SalaryCalculation.payment_status.in_(['unpaid', 'partial'])
        )
    ).order_by(SalaryCalculation.calculated_at.desc()).first()
    return unpaid_salary
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
