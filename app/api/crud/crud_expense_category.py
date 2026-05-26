from typing import List, Optional
from sqlalchemy.orm import Session
from sqlalchemy import or_
from app.db.models.expense_category import ExpenseCategory
from app.schemas.expense_category import ExpenseCategoryCreate, ExpenseCategoryUpdate
def create_expense_category(db: Session, category_in: ExpenseCategoryCreate) -> ExpenseCategory:
    category = ExpenseCategory(**category_in.dict())
    db.add(category)
    db.commit()
    db.refresh(category)
    return category
def get_expense_category(db: Session, category_id: int, business_id: Optional[int] = None) -> Optional[ExpenseCategory]:
    query = db.query(ExpenseCategory).filter(ExpenseCategory.id == category_id)
    if business_id:
        query = query.filter(
            or_(
                ExpenseCategory.is_system == True,
                ExpenseCategory.business_id == business_id
            )
        )
    return query.first()
def list_expense_categories(
    db: Session,
    business_id: Optional[int] = None,
    is_system: Optional[bool] = None,
    is_active: Optional[bool] = True,
    skip: int = 0,
    limit: int = 100,
) -> List[ExpenseCategory]:
    query = db.query(ExpenseCategory)
    if business_id:
        query = query.filter(
            or_(
                ExpenseCategory.is_system == True,
                ExpenseCategory.business_id == business_id
            )
        )
    if is_system is not None:
        query = query.filter(ExpenseCategory.is_system == is_system)
    if is_active is not None:
        query = query.filter(ExpenseCategory.is_active == is_active)
    return query.order_by(ExpenseCategory.is_system.desc(), ExpenseCategory.name).offset(skip).limit(limit).all()
def update_expense_category(db: Session, category: ExpenseCategory, category_in: ExpenseCategoryUpdate) -> ExpenseCategory:
    if category.is_system:
        raise ValueError("Cannot update system category")
    update_data = category_in.dict(exclude_unset=True)
    for field, value in update_data.items():
        setattr(category, field, value)
    db.commit()
    db.refresh(category)
    return category
def delete_expense_category(db: Session, category_id: int, business_id: Optional[int] = None) -> bool:
    category = get_expense_category(db, category_id, business_id)
    if not category:
        return False
    if category.is_system:
        raise ValueError("Cannot delete system category")
    db.delete(category)
    db.commit()
    return True
