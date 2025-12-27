from typing import Optional, List, Tuple
from datetime import datetime

from sqlalchemy.orm import Session
from sqlalchemy import and_, func

from app.api.core.pagination import PaginationParams
from app.db.models.salary_calculation import SalaryCalculation
from app.db.models.employee import Employee
from app.db.models.visit import Visit
from app.api.crud import crud_employee
from app.schemas.salary import SalaryCalculationCreate, SalaryCalculationUpdate


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
    
    # Итоговая зарплата
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
    pagination: Optional[PaginationParams] = None
) -> Tuple[List[SalaryCalculation], int]:
    """Получить список расчетов зарплат."""
    query = db.query(SalaryCalculation)
    
    # Фильтр по сотруднику
    if employee_id is not None:
        query = query.filter(SalaryCalculation.employee_id == employee_id)
    
    # Фильтр по статусу выплаты
    if is_paid is not None:
        query = query.filter(SalaryCalculation.is_paid == is_paid)
    
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
    elif update_data.get('is_paid') is False:
        update_data['paid_at'] = None
    
    for field, value in update_data.items():
        setattr(salary_calculation, field, value)
    
    salary_calculation.updated_at = datetime.utcnow()
    
    db.add(salary_calculation)
    db.commit()
    db.refresh(salary_calculation)
    
    return salary_calculation


def get_current_unpaid_salary(
    db: Session,
    employee_user_id: int
) -> Optional[SalaryCalculation]:
    """
    Получить текущую невыплаченную зарплату для сотрудника.
    Если невыплаченных зарплат несколько, возвращает самую новую.
    """
    # Находим Employee по user_id
    employee = crud_employee.get_employee_by_user_id(db, employee_user_id)
    if not employee:
        return None
    
    # Ищем невыплаченную зарплату
    unpaid_salary = db.query(SalaryCalculation).filter(
        and_(
            SalaryCalculation.employee_id == employee.id,
            SalaryCalculation.is_paid == False
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

