from typing import List, Optional, Dict
from datetime import datetime
from sqlalchemy.orm import Query, Session
from sqlalchemy import and_, func
from app.db.models.expense import Expense
from app.schemas.expense import ExpenseCreate, ExpenseUpdate
def create_expense(
    db: Session,
    expense_in: ExpenseCreate,
    created_by_id: Optional[int] = None,
    source: Optional[str] = None,
    employee_id: Optional[int] = None,
) -> Expense:
    expense_data = expense_in.model_dump()
    expense_data["created_by_id"] = created_by_id
    expense_data["source"] = source
    expense_data["employee_id"] = employee_id
    expense = Expense(**expense_data)
    db.add(expense)
    db.commit()
    db.refresh(expense)
    return expense
def get_expense(db: Session, expense_id: int, business_id: Optional[int] = None) -> Optional[Expense]:
    query = db.query(Expense).filter(Expense.id == expense_id)
    if business_id:
        query = query.filter(Expense.business_id == business_id)
    return query.first()
def query_expenses(
    db: Session,
    business_id: Optional[int] = None,
    category_id: Optional[int] = None,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    payment_method: Optional[str] = None,
    min_amount: Optional[float] = None,
    max_amount: Optional[float] = None,
) -> Query[Expense]:
    query = db.query(Expense)
    if business_id:
        query = query.filter(Expense.business_id == business_id)
    if category_id:
        query = query.filter(Expense.category_id == category_id)
    if date_from:
        query = query.filter(Expense.expense_date >= date_from)
    if date_to:
        query = query.filter(Expense.expense_date <= date_to)
    if payment_method:
        query = query.filter(Expense.payment_method == payment_method)
    if min_amount is not None:
        query = query.filter(Expense.amount >= min_amount)
    if max_amount is not None:
        query = query.filter(Expense.amount <= max_amount)
    return query.order_by(Expense.expense_date.desc(), Expense.id.desc())
def list_expenses(
    db: Session,
    business_id: Optional[int] = None,
    category_id: Optional[int] = None,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    payment_method: Optional[str] = None,
    min_amount: Optional[float] = None,
    max_amount: Optional[float] = None,
    skip: int = 0,
    limit: int = 100,
) -> List[Expense]:
    return query_expenses(
        db, business_id=business_id, category_id=category_id, date_from=date_from,
        date_to=date_to, payment_method=payment_method, min_amount=min_amount, max_amount=max_amount,
    ).offset(skip).limit(limit).all()
def update_expense(db: Session, expense: Expense, expense_in: ExpenseUpdate) -> Expense:
    update_data = expense_in.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(expense, field, value)
    db.commit()
    db.refresh(expense)
    return expense
def delete_expense(db: Session, expense_id: int, business_id: Optional[int] = None) -> bool:
    expense = get_expense(db, expense_id, business_id)
    if not expense:
        return False
    db.delete(expense)
    db.commit()
    return True
def get_expenses_total(
    db: Session,
    business_id: Optional[int] = None,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    category_id: Optional[int] = None,
) -> float:
    query = db.query(func.sum(Expense.amount))
    if business_id:
        query = query.filter(Expense.business_id == business_id)
    if category_id:
        query = query.filter(Expense.category_id == category_id)
    if date_from:
        query = query.filter(Expense.expense_date >= date_from)
    if date_to:
        query = query.filter(Expense.expense_date <= date_to)
    result = query.scalar()
    return float(result) if result else 0.0
def get_expenses_by_category(
    db: Session,
    business_id: Optional[int] = None,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
) -> List[Dict]:
    from app.db.models.expense_category import ExpenseCategory
    query = db.query(
        ExpenseCategory.id,
        ExpenseCategory.name,
        ExpenseCategory.code,
        ExpenseCategory.color,
        func.sum(Expense.amount).label('total')
    ).join(
        Expense, Expense.category_id == ExpenseCategory.id, isouter=True
    )
    if business_id:
        query = query.filter(
            and_(
                (Expense.business_id == business_id) | (Expense.business_id.is_(None)),
                (ExpenseCategory.business_id == business_id) | (ExpenseCategory.business_id.is_(None)) | (ExpenseCategory.is_system == True)
            )
        )
    if date_from:
        query = query.filter((Expense.expense_date >= date_from) | (Expense.expense_date.is_(None)))
    if date_to:
        query = query.filter((Expense.expense_date <= date_to) | (Expense.expense_date.is_(None)))
    query = query.group_by(ExpenseCategory.id, ExpenseCategory.name, ExpenseCategory.code, ExpenseCategory.color)
    query = query.having(func.sum(Expense.amount) > 0)
    results = query.all()
    return [
        {
            "category_id": r.id,
            "category_name": r.name,
            "category_code": r.code,
            "color": r.color,
            "total": float(r.total) if r.total else 0.0
        }
        for r in results
    ]
def get_expenses_daily(
    db: Session,
    business_id: Optional[int] = None,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
) -> List[Dict]:
    query = db.query(
        func.date(Expense.expense_date).label('date'),
        func.sum(Expense.amount).label('total')
    )
    if business_id:
        query = query.filter(Expense.business_id == business_id)
    if date_from:
        query = query.filter(Expense.expense_date >= date_from)
    if date_to:
        query = query.filter(Expense.expense_date <= date_to)
    query = query.group_by(func.date(Expense.expense_date))
    query = query.order_by(func.date(Expense.expense_date))
    results = query.all()
    return [
        {
            "date": r.date.isoformat() if hasattr(r.date, 'isoformat') else str(r.date),
            "total": float(r.total) if r.total else 0.0
        }
        for r in results
    ]
