from typing import Optional, List, Tuple
from datetime import datetime

from sqlalchemy.orm import Session
from sqlalchemy import and_, func

from app.api.core.pagination import PaginationParams
from app.db.models.salary_calculation import SalaryCalculation
from app.db.models.salary_adjustment import SalaryAdjustment
from app.db.models.salary_payment import SalaryPayment
from app.db.models.employee import Employee
from app.db.models.visit import Visit
from app.api.crud import crud_employee
from app.schemas.salary import (
    SalaryCalculationCreate,
    SalaryCalculationUpdate,
    SalaryAdjustmentCreate,
    SalaryPaymentCreate,
    SalaryPaymentUpdate
)


def calculate_salary(
    db: Session,
    employee_id: int,
    period_start: datetime,
    period_end: datetime,
    calculated_by_id: Optional[int] = None,
    notes: Optional[str] = None
) -> SalaryCalculation:
    """
    Рассчитывает зарплату сотрудника за указанный период на основе его визитов.
    
    Args:
        db: Database session
        employee_id: ID сотрудника (Employee.id)
        period_start: Начало периода
        period_end: Конец периода
        calculated_by_id: ID пользователя, который рассчитывает (admin/business_owner)
        notes: Заметки к расчету
    
    Returns:
        SalaryCalculation объект с результатами расчета
    """
    # Получаем информацию о сотруднике
    employee = db.query(Employee).filter(Employee.id == employee_id).first()
    if not employee:
        raise ValueError(f"Employee with id={employee_id} not found")
    
    if not employee.is_active:
        raise ValueError(f"Employee with id={employee_id} is not active")
    
    # Получаем визиты сотрудника за период
    visits_query = db.query(Visit).filter(
        and_(
            Visit.specialist_id == employee.employee_user_id,
            Visit.date_start >= period_start,
            Visit.date_start < period_end
        )
    )
    
    # Подсчитываем статистику по визитам
    total_visits = visits_query.count()
    completed_visits = visits_query.filter(Visit.status == 'completed').count()
    cancelled_visits = visits_query.filter(Visit.status == 'cancelled').count()
    
    # Подсчитываем общую сумму визитов (только завершенные с ценой)
    revenue_result = visits_query.filter(
        and_(
            Visit.status == 'completed',
            Visit.price.isnot(None)
        )
    ).with_entities(func.sum(Visit.price)).scalar()
    
    total_revenue = float(revenue_result) if revenue_result else 0.0
    
    # Получаем параметры зарплаты из Employee
    commission_percentage = employee.commission_percentage or 0.0
    base_salary = employee.base_salary or 0.0
    
    # Рассчитываем комиссию
    commission_amount = (total_revenue * commission_percentage) / 100.0 if commission_percentage > 0 else 0.0
    
    # Итоговая зарплата (базовая + комиссия, без adjustments)
    total_salary = base_salary + commission_amount
    
    # Создаем запись расчета зарплаты
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
    """Получить информацию о расчете зарплаты."""
    query = db.query(SalaryCalculation).filter(SalaryCalculation.id == salary_id)
    
    if employee_id is not None:
        query = query.filter(SalaryCalculation.employee_id == employee_id)
    
    return query.options(
        # Можно добавить joinedload для employee, если нужно
    ).first()


def list_salary_calculations(
    db: Session,
    employee_id: Optional[int] = None,
    is_paid: Optional[bool] = None,
    payment_status: Optional[str] = None,
    pagination: Optional[PaginationParams] = None
) -> Tuple[List[SalaryCalculation], int]:
    """Получить список расчетов зарплат."""
    query = db.query(SalaryCalculation)
    
    # Фильтр по сотруднику
    if employee_id is not None:
        query = query.filter(SalaryCalculation.employee_id == employee_id)
    
    # Фильтр по статусу выплаты (старый способ для совместимости)
    if is_paid is not None:
        query = query.filter(SalaryCalculation.is_paid == is_paid)
    
    # Фильтр по новому статусу выплаты
    if payment_status is not None:
        query = query.filter(SalaryCalculation.payment_status == payment_status)
    
    # Сортируем по дате расчета (новые сначала)
    query = query.order_by(SalaryCalculation.calculated_at.desc())
    
    # Применяем пагинацию
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
    """Обновить информацию о расчете зарплаты (например, отметить как выплаченную)."""
    update_data = update.model_dump(exclude_unset=True)
    
    # Если отмечаем как выплаченную, устанавливаем дату выплаты
    if update_data.get('is_paid') is True and not salary_calculation.is_paid:
        update_data['paid_at'] = datetime.utcnow()
        update_data['payment_status'] = 'paid'
        update_data['paid_amount'] = salary_calculation.total_with_adjustments
    elif update_data.get('is_paid') is False:
        update_data['paid_at'] = None
        update_data['payment_status'] = 'unpaid'
        update_data['paid_amount'] = 0.0
    
    for field, value in update_data.items():
        setattr(salary_calculation, field, value)
    
    salary_calculation.updated_at = datetime.utcnow()
    
    db.add(salary_calculation)
    db.commit()
    db.refresh(salary_calculation)
    
    return salary_calculation


def _update_salary_payment_status(db: Session, salary_calculation: SalaryCalculation):
    """Вспомогательная функция для обновления статуса выплаты на основе payments."""
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
            salary_calculation.paid_at = datetime.utcnow()
    elif salary_calculation.paid_amount > 0:
        salary_calculation.payment_status = 'partial'
        salary_calculation.is_paid = False
    else:
        salary_calculation.payment_status = 'unpaid'
        salary_calculation.is_paid = False
    
    db.commit()
    db.refresh(salary_calculation)


# ============ SalaryAdjustment CRUD ============

def create_salary_adjustment(
    db: Session,
    salary_calculation_id: int,
    adjustment_in: SalaryAdjustmentCreate,
    created_by_id: int
) -> SalaryAdjustment:
    """Создать начисление/удержание к зарплате."""
    adjustment = SalaryAdjustment(
        salary_calculation_id=salary_calculation_id,
        type=adjustment_in.type,
        amount=adjustment_in.amount,
        description=adjustment_in.description,
        created_by_id=created_by_id
    )
    db.add(adjustment)
    db.flush()
    
    # Пересчитываем adjustments_total и total_with_adjustments
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
    """Получить начисление/удержание."""
    query = db.query(SalaryAdjustment).filter(SalaryAdjustment.id == adjustment_id)
    
    if salary_calculation_id is not None:
        query = query.filter(SalaryAdjustment.salary_calculation_id == salary_calculation_id)
    
    return query.first()


def list_salary_adjustments(
    db: Session,
    salary_calculation_id: int,
    adjustment_type: Optional[str] = None
) -> List[SalaryAdjustment]:
    """Получить список начислений/удержаний для расчета зарплаты."""
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
    """Удалить начисление/удержание."""
    adjustment = get_salary_adjustment(db, adjustment_id, salary_calculation_id)
    if not adjustment:
        return False
    
    calc_id = adjustment.salary_calculation_id
    db.delete(adjustment)
    db.flush()
    
    # Пересчитываем adjustments_total
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


# ============ SalaryPayment CRUD ============

def create_salary_payment(
    db: Session,
    salary_calculation_id: int,
    payment_in: SalaryPaymentCreate,
    created_by_id: int,
    business_id: Optional[int] = None
) -> SalaryPayment:
    """Создать выплату."""
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
    
    # Обновляем статус выплаты в salary_calculation
    salary_calculation = get_salary_calculation(db, salary_calculation_id)
    if salary_calculation:
        _update_salary_payment_status(db, salary_calculation)
    
    # Если выплата завершена (status='completed'), создаем расход за месяц выплаты
    if payment_in.status == 'completed' and business_id:
        from app.api.crud import crud_expense, crud_expense_category
        from app.schemas.expense import ExpenseCreate
        from datetime import datetime
        
        # Ищем или создаем категорию "Зарплаты" (Salary/Зарплата)
        salary_category = crud_expense_category.list_expense_categories(
            db, 
            business_id=business_id,
            skip=0,
            limit=100
        )
        salary_category = next(
            (cat for cat in salary_category if 'зарплат' in cat.name.lower() or 'salary' in cat.name.lower() or 'plat' in cat.name.lower()),
            None
        )
        
        # Если категории нет, создаем системную категорию (если возможно) или пользовательскую
        category_id = None
        if not salary_category:
            try:
                # Пробуем создать категорию
                from app.schemas.expense_category import ExpenseCategoryCreate
                new_category = ExpenseCategoryCreate(
                    name="Зарплата",
                    color="#FF6B6B",
                    business_id=business_id,
                    is_system=False
                )
                salary_category = crud_expense_category.create_expense_category(db, new_category)
                category_id = salary_category.id
            except Exception as e:
                # Если не удалось создать, работаем без категории
                pass
        else:
            category_id = salary_category.id
        
        # Создаем расход на дату выплаты
        expense_date = payment_in.payment_date
        expense_description = f"Выплата зарплаты сотруднику (ID: {salary_calculation.employee_id})"
        if payment_in.notes:
            expense_description += f" - {payment_in.notes}"
        
        expense = ExpenseCreate(
            business_id=business_id,
            category_id=category_id,
            amount=payment_in.amount,
            currency="EUR",  # TODO: получить из бизнеса
            description=expense_description,
            expense_date=expense_date,
            payment_method=payment_in.payment_method or "other"
        )
        
        try:
            crud_expense.create_expense(db, expense, created_by_id=created_by_id)
        except Exception as e:
            # Логируем ошибку, но не прерываем создание выплаты
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
    """Получить выплату."""
    query = db.query(SalaryPayment).filter(SalaryPayment.id == payment_id)
    
    if salary_calculation_id is not None:
        query = query.filter(SalaryPayment.salary_calculation_id == salary_calculation_id)
    
    return query.first()


def list_salary_payments(
    db: Session,
    salary_calculation_id: int,
    status: Optional[str] = None
) -> List[SalaryPayment]:
    """Получить список выплат для расчета зарплаты."""
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
    """Обновить выплату."""
    update_data = update.model_dump(exclude_unset=True)
    
    for field, value in update_data.items():
        setattr(payment, field, value)
    
    payment.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(payment)
    
    # Обновляем статус выплаты в salary_calculation
    salary_calculation = get_salary_calculation(db, payment.salary_calculation_id)
    if salary_calculation:
        _update_salary_payment_status(db, salary_calculation)
    
    return payment


def delete_salary_payment(
    db: Session,
    payment_id: int,
    salary_calculation_id: Optional[int] = None
) -> bool:
    """Удалить выплату."""
    payment = get_salary_payment(db, payment_id, salary_calculation_id)
    if not payment:
        return False
    
    calc_id = payment.salary_calculation_id
    db.delete(payment)
    db.flush()
    
    # Обновляем статус выплаты в salary_calculation
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
    """
    Получить текущую невыплаченную зарплату для сотрудника.
    Если невыплаченных зарплат несколько, возвращает самую новую.
    
    Args:
        employee_user_id: ID пользователя-сотрудника (User.id)
        employee_id: ID сотрудника (Employee.id) - приоритетнее, если указан
    """
    # Если указан employee_id, используем его напрямую
    if employee_id is not None:
        target_employee_id = employee_id
    elif employee_user_id is not None:
        # Находим Employee по user_id
        employee = crud_employee.get_employee_by_user_id(db, employee_user_id)
        if not employee:
            return None
        target_employee_id = employee.id
    else:
        return None
    
    # Ищем невыплаченную зарплату (unpaid или partial)
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
    """Получить историю зарплат сотрудника."""
    employee = crud_employee.get_employee_by_user_id(db, employee_user_id)
    if not employee:
        return []
    
    return db.query(SalaryCalculation).filter(
        SalaryCalculation.employee_id == employee.id
    ).order_by(SalaryCalculation.calculated_at.desc()).limit(limit).all()

