from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from app.api import deps
from app.schemas.datetimes import UtcDatetime
from app.api.core.i18n import _
from app.api.core.pagination import MAX_PAGE, PaginatedResponse
from app.db.models.user import User
from app.schemas.expense import ExpenseCategoryTotal, ExpenseCreate, ExpenseUpdate, ExpenseOut, ProfitReportResponse
from app.schemas.expense_category import ExpenseCategoryCreate, ExpenseCategoryUpdate, ExpenseCategoryOut
from app.api.crud import crud_expense, crud_expense_category
from app.api.core.logger import logger
from app.api.core.clock import utcnow
from app.api.core.permissions import can_view_financials, require_business_id
router = APIRouter()
def _finance_business_id(current_user: User, db: Session, locale: str) -> int:
    # money is for the owner, admins and staff with the "view financials" box ticked
    if not can_view_financials(current_user, db):
        raise HTTPException(status_code=403, detail=_("not_allowed", locale))
    return require_business_id(current_user, db, locale)
def _check_category(db: Session, category_id, business_id: int, locale: str) -> None:
    # a category is ours, or one of the shared system ones - never another business's
    if category_id is None:
        return
    from app.db.models.expense_category import ExpenseCategory
    cat = db.query(ExpenseCategory).filter(ExpenseCategory.id == category_id).first()
    if not cat or not (cat.business_id == business_id or (cat.business_id is None and cat.is_system)):
        raise HTTPException(status_code=403, detail=_("not_allowed", locale))
@router.post("/categories", response_model=ExpenseCategoryOut)
def create_expense_category(
    category: ExpenseCategoryCreate,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.role_required("admin", "business_owner")),
    locale: str = Depends(deps.get_locale),
):
    business_id = _finance_business_id(current_user, db, locale)
    if category.business_id and category.business_id != business_id:
        raise HTTPException(status_code=403, detail=_("not_allowed", locale))
    if not category.business_id:
        category.business_id = business_id
    try:
        result = crud_expense_category.create_expense_category(db, category)
        logger.info(f"Created expense category {result.id} by user {current_user.id}")
        return result
    except ValueError as e:
        logger.error(f"Error creating expense category: {e}")
        raise HTTPException(status_code=400, detail=str(e))
@router.get("/categories", response_model=List[ExpenseCategoryOut])
def list_expense_categories(
    is_system: Optional[bool] = Query(None),
    is_active: Optional[bool] = Query(True),
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.role_required("admin", "business_owner", "specialist", "employee")),
    locale: str = Depends(deps.get_locale),
):
    business_id = _finance_business_id(current_user, db, locale)
    categories = crud_expense_category.list_expense_categories(
        db,
        business_id=business_id,
        is_system=is_system,
        is_active=is_active,
    )
    return [c for c in categories]
@router.put("/categories/{category_id}", response_model=ExpenseCategoryOut)
def update_expense_category(
    category_id: int,
    category: ExpenseCategoryUpdate,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.role_required("admin", "business_owner")),
    locale: str = Depends(deps.get_locale),
):
    business_id = _finance_business_id(current_user, db, locale)
    db_category = crud_expense_category.get_expense_category(db, category_id, business_id)
    if not db_category:
        raise HTTPException(status_code=404, detail=_("category_not_found", locale))
    try:
        result = crud_expense_category.update_expense_category(db, db_category, category)
        logger.info(f"Updated expense category {category_id} by user {current_user.id}")
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
@router.delete("/categories/{category_id}")
def delete_expense_category(
    category_id: int,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.role_required("admin", "business_owner")),
    locale: str = Depends(deps.get_locale),
):
    business_id = _finance_business_id(current_user, db, locale)
    try:
        success = crud_expense_category.delete_expense_category(db, category_id, business_id)
        if not success:
            raise HTTPException(status_code=404, detail=_("category_not_found", locale))
        logger.info(f"Deleted expense category {category_id} by user {current_user.id}")
        return {"message": _("category_deleted", locale)}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
@router.post("/", response_model=ExpenseOut)
def create_expense(
    expense: ExpenseCreate,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.role_required("admin", "business_owner", "specialist", "employee")),
    locale: str = Depends(deps.get_locale),
):
    business_id = _finance_business_id(current_user, db, locale)
    if expense.business_id != business_id:
        raise HTTPException(status_code=403, detail=_("not_allowed", locale))
    _check_category(db, expense.category_id, business_id, locale)
    try:
        result = crud_expense.create_expense(db, expense, created_by_id=current_user.id)
        logger.info(f"Created expense {result.id} by user {current_user.id}")
        return result
    except ValueError as e:
        logger.error(f"Error creating expense: {e}")
        raise HTTPException(status_code=400, detail=str(e))
@router.get("/", response_model=PaginatedResponse[ExpenseOut])
def list_expenses(
    category_id: Optional[int] = Query(None),
    date_from: Optional[UtcDatetime] = Query(None),
    date_to: Optional[UtcDatetime] = Query(None),
    payment_method: Optional[str] = Query(None),
    min_amount: Optional[float] = Query(None),
    max_amount: Optional[float] = Query(None),
    page: int = Query(1, ge=1, le=MAX_PAGE),
    per_page: int = Query(20, ge=1, le=100),
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.role_required("admin", "business_owner", "specialist", "employee")),
    locale: str = Depends(deps.get_locale),
):
    business_id = _finance_business_id(current_user, db, locale)
    skip = (page - 1) * per_page
    query = crud_expense.query_expenses(
        db,
        business_id=business_id,
        category_id=category_id,
        date_from=date_from,
        date_to=date_to,
        payment_method=payment_method,
        min_amount=min_amount,
        max_amount=max_amount,
    )
    total = query.order_by(None).count()
    expenses = query.offset(skip).limit(per_page).all()
    return PaginatedResponse.create(
        items=[e for e in expenses],
        total=total,
        page=page,
        per_page=per_page,
    )
@router.get("/{expense_id}", response_model=ExpenseOut)
def get_expense(
    expense_id: int,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.role_required("admin", "business_owner", "specialist", "employee")),
    locale: str = Depends(deps.get_locale),
):
    business_id = _finance_business_id(current_user, db, locale)
    expense = crud_expense.get_expense(db, expense_id, business_id)
    if not expense:
        raise HTTPException(status_code=404, detail=_("expense_not_found", locale))
    return expense
@router.put("/{expense_id}", response_model=ExpenseOut)
def update_expense(
    expense_id: int,
    expense: ExpenseUpdate,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.role_required("admin", "business_owner")),
    locale: str = Depends(deps.get_locale),
):
    business_id = _finance_business_id(current_user, db, locale)
    db_expense = crud_expense.get_expense(db, expense_id, business_id)
    if not db_expense:
        raise HTTPException(status_code=404, detail=_("expense_not_found", locale))
    _check_category(db, expense.category_id, business_id, locale)
    try:
        result = crud_expense.update_expense(db, db_expense, expense)
        logger.info(f"Updated expense {expense_id} by user {current_user.id}")
        return result
    except ValueError as e:
        logger.error(f"Error updating expense: {e}")
        raise HTTPException(status_code=400, detail=str(e))
@router.delete("/{expense_id}")
def delete_expense(
    expense_id: int,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.role_required("admin", "business_owner")),
    locale: str = Depends(deps.get_locale),
):
    business_id = _finance_business_id(current_user, db, locale)
    success = crud_expense.delete_expense(db, expense_id, business_id)
    if not success:
        raise HTTPException(status_code=404, detail=_("expense_not_found", locale))
    logger.info(f"Deleted expense {expense_id} by user {current_user.id}")
    return {"message": _("expense_deleted", locale)}
@router.get("/reports/profit", response_model=ProfitReportResponse)
def get_profit_report(
    date_from: Optional[UtcDatetime] = Query(None),
    date_to: Optional[UtcDatetime] = Query(None),
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.role_required("admin", "business_owner", "specialist", "employee")),
    locale: str = Depends(deps.get_locale),
):
    from datetime import datetime, timedelta
    from app.db.models.visit import Visit
    from sqlalchemy import func
    business_id = _finance_business_id(current_user, db, locale)
    if not date_from or not date_to:
        now = utcnow()
        date_from = datetime(now.year, now.month, 1)
        if now.month == 12:
            date_to = datetime(now.year + 1, 1, 1) - timedelta(seconds=1)
        else:
            date_to = datetime(now.year, now.month + 1, 1) - timedelta(seconds=1)
    revenue_query = db.query(func.sum(Visit.price)).filter(
        Visit.business_id == business_id,
        Visit.status == 'completed',
        Visit.date_start >= date_from,
        Visit.date_start <= date_to,
        Visit.price.isnot(None),
    )
    revenue = float(revenue_query.scalar() or 0.0)
    expenses = crud_expense.get_expenses_total(
        db,
        business_id=business_id,
        date_from=date_from,
        date_to=date_to,
    )
    profit = revenue - expenses
    expenses_by_category = crud_expense.get_expenses_by_category(
        db,
        business_id=business_id,
        date_from=date_from,
        date_to=date_to,
    )
    expenses_daily = crud_expense.get_expenses_daily(
        db,
        business_id=business_id,
        date_from=date_from,
        date_to=date_to,
    )
    revenue_daily_query = db.query(
        func.date(Visit.date_start).label('date'),
        func.sum(Visit.price).label('total')
    ).filter(
        Visit.business_id == business_id,
        Visit.status == 'completed',
        Visit.date_start >= date_from,
        Visit.date_start <= date_to,
        Visit.price.isnot(None),
    ).group_by(func.date(Visit.date_start)).order_by(func.date(Visit.date_start))
    revenue_daily = [
        {
            "date": r.date.isoformat() if hasattr(r.date, 'isoformat') else str(r.date),
            "total": float(r.total) if r.total else 0.0
        }
        for r in revenue_daily_query.all()
    ]
    profit_daily = []
    expense_dict = {e["date"]: e["total"] for e in expenses_daily}
    revenue_dict = {r["date"]: r["total"] for r in revenue_daily}
    all_dates = set(list(expense_dict.keys()) + list(revenue_dict.keys()))
    for date in sorted(all_dates):
        profit_daily.append({
            "date": date,
            "revenue": revenue_dict.get(date, 0.0),
            "expenses": expense_dict.get(date, 0.0),
            "profit": revenue_dict.get(date, 0.0) - expense_dict.get(date, 0.0),
        })
    return {
        "period": {"from": date_from, "to": date_to},
        "revenue": revenue,
        "expenses": expenses,
        "profit": profit,
        "profit_margin": (profit / revenue * 100) if revenue > 0 else 0.0,
        "expenses_by_category": expenses_by_category,
        "daily": profit_daily,
    }
@router.get("/reports/expenses-by-category", response_model=List[ExpenseCategoryTotal])
def get_expenses_by_category(
    date_from: Optional[UtcDatetime] = Query(None),
    date_to: Optional[UtcDatetime] = Query(None),
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.role_required("admin", "business_owner", "specialist", "employee")),
    locale: str = Depends(deps.get_locale),
):
    business_id = _finance_business_id(current_user, db, locale)
    result = crud_expense.get_expenses_by_category(
        db,
        business_id=business_id,
        date_from=date_from,
        date_to=date_to,
    )
    return result
