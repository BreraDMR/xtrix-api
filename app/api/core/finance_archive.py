from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional
from sqlalchemy.orm import Session
from sqlalchemy import func
from app.db.models.visit import Visit
from app.api.crud import crud_expense
from app.api.core.logger import logger
def archive_monthly_financial_data(
    db: Session,
    year: int,
    month: int,
    business_id: Optional[int] = None,
) -> Dict[str, Any]:
    date_from = datetime(year, month, 1)
    if month == 12:
        date_to = datetime(year + 1, 1, 1) - timedelta(seconds=1)
    else:
        date_to = datetime(year, month + 1, 1) - timedelta(seconds=1)
    logger.info(f"Archiving financial data for {year}-{month:02d}, business_id={business_id}")
    revenue_query = db.query(func.sum(Visit.price)).filter(
        Visit.status == 'completed',
        Visit.date_start >= date_from,
        Visit.date_start <= date_to,
        Visit.price.isnot(None),
    )
    if business_id:
        revenue_query = revenue_query.filter(Visit.business_id == business_id)
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
    revenue_daily_query = db.query(
        func.date(Visit.date_start).label('date'),
        func.sum(Visit.price).label('total'),
        func.count(Visit.id).label('count')
    ).filter(
        Visit.status == 'completed',
        Visit.date_start >= date_from,
        Visit.date_start <= date_to,
        Visit.price.isnot(None),
    )
    if business_id:
        revenue_daily_query = revenue_daily_query.filter(Visit.business_id == business_id)
    revenue_daily_query = revenue_daily_query.group_by(func.date(Visit.date_start)).order_by(func.date(Visit.date_start))
    revenue_daily = [
        {
            "date": r.date.isoformat() if hasattr(r.date, 'isoformat') else str(r.date),
            "revenue": float(r.total) if r.total else 0.0,
            "visit_count": int(r.count) if r.count else 0,
        }
        for r in revenue_daily_query.all()
    ]
    expenses_daily = crud_expense.get_expenses_daily(
        db,
        business_id=business_id,
        date_from=date_from,
        date_to=date_to,
    )
    profit_daily = []
    expense_dict = {e["date"]: e["total"] for e in expenses_daily}
    revenue_dict = {r["date"]: r["revenue"] for r in revenue_daily}
    all_dates = set(list(expense_dict.keys()) + list(revenue_dict.keys()))
    for date in sorted(all_dates):
        profit_daily.append({
            "date": date,
            "revenue": revenue_dict.get(date, 0.0),
            "expenses": expense_dict.get(date, 0.0),
            "profit": revenue_dict.get(date, 0.0) - expense_dict.get(date, 0.0),
        })
    visits_query = db.query(func.count(Visit.id)).filter(
        Visit.date_start >= date_from,
        Visit.date_start <= date_to,
    )
    if business_id:
        visits_query = visits_query.filter(Visit.business_id == business_id)
    total_visits = visits_query.scalar() or 0
    completed_visits = db.query(func.count(Visit.id)).filter(
        Visit.status == 'completed',
        Visit.date_start >= date_from,
        Visit.date_start <= date_to,
    )
    if business_id:
        completed_visits = completed_visits.filter(Visit.business_id == business_id)
    completed_visits = completed_visits.scalar() or 0
    archive_data = {
        "period": {
            "year": year,
            "month": month,
            "from": date_from.isoformat(),
            "to": date_to.isoformat(),
        },
        "business_id": business_id,
        "summary": {
            "revenue": revenue,
            "expenses": expenses,
            "profit": profit,
            "profit_margin": (profit / revenue * 100) if revenue > 0 else 0.0,
            "total_visits": total_visits,
            "completed_visits": completed_visits,
        },
        "expenses_by_category": expenses_by_category,
        "daily": profit_daily,
        "archived_at": datetime.utcnow().isoformat(),
    }
    logger.info(
        f"Archived financial data for {year}-{month:02d}: "
        f"revenue={revenue:.2f}, expenses={expenses:.2f}, profit={profit:.2f}"
    )
    return archive_data
def archive_all_businesses_last_month(db: Session) -> List[Dict[str, Any]]:
    from app.db.models.business import Business
    now = datetime.utcnow()
    if now.month == 1:
        last_month = 12
        last_year = now.year - 1
    else:
        last_month = now.month - 1
        last_year = now.year
    logger.info(f"Starting monthly archive for {last_year}-{last_month:02d}")
    businesses = db.query(Business).filter(Business.deleted_at.is_(None)).all()
    archives = []
    for business in businesses:
        try:
            archive = archive_monthly_financial_data(
                db,
                year=last_year,
                month=last_month,
                business_id=business.id,
            )
            archives.append(archive)
        except Exception as e:
            logger.error(f"Error archiving data for business {business.id}: {e}")
            continue
    logger.info(f"Completed monthly archive: {len(archives)} businesses archived")
    return archives
def should_run_monthly_archive(db: Session) -> bool:
    from app.db.models.audit_log import AuditLog
    now = datetime.utcnow()
    if now.day != 1:
        return False
    today_start = datetime(now.year, now.month, 1)
    existing_log = db.query(AuditLog).filter(
        AuditLog.action == 'monthly_finance_archive',
        AuditLog.created_at >= today_start,
    ).first()
    return existing_log is None
