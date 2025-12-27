"""Analytics and reporting endpoints."""
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api import deps
from app.api.core.i18n import _
from app.api.core.analytics import (
    get_visit_statistics,
    get_financial_report,
    get_popular_services,
    get_client_statistics,
)
from app.api.core.redis_client import (
    cache_analytics, get_cached_analytics, invalidate_analytics,
)
from app.api.core.export import export_to_csv, export_to_excel
from app.db.models.user import User
from app.schemas.analytics import (
    VisitStatisticsResponse,
    FinancialReportResponse,
    PopularServicesResponse,
    ClientStatisticsResponse,
    ReportExportRequest,
)

router = APIRouter()


@router.get("/visits/statistics", response_model=VisitStatisticsResponse)
def get_visits_statistics(
    specialist_id: Optional[int] = Query(None),
    client_id: Optional[int] = Query(None),
    date_from: Optional[datetime] = Query(None),
    date_to: Optional[datetime] = Query(None),
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.role_required("admin", "business_owner", "specialist", "employee")),
    locale: str = Depends(deps.get_locale),
):
    """Get visit statistics."""
    from app.api.core.permissions import get_filtered_specialist_id
    
    # Фильтрация по ролям
    if current_user.role == "admin":
        # Админ может видеть все, но может фильтровать по specialist_id
        pass
    elif current_user.role == "business_owner":
        # Владелец бизнеса видит статистику всего бизнеса
        specialist_id = None
    else:
        # specialist и employee видят только свою статистику
        specialist_id = get_filtered_specialist_id(current_user)
    
    # Check cache
    cache_key = {
        'specialist_id': specialist_id,
        'client_id': client_id,
        'date_from': date_from.isoformat() if date_from is not None else None,
        'date_to': date_to.isoformat() if date_to is not None else None,
    }
    cached = get_cached_analytics('visits_statistics', **cache_key)
    if cached:
        return VisitStatisticsResponse(**cached)
    
    stats = get_visit_statistics(
        db,
        specialist_id=specialist_id,
        client_id=client_id,
        date_from=date_from,
        date_to=date_to,
    )
    
    # Cache result
    cache_analytics('visits_statistics', stats, ttl_seconds=300, **cache_key)
    
    return VisitStatisticsResponse(**stats)


@router.get("/financial/report", response_model=FinancialReportResponse)
def get_financial_report_endpoint(
    specialist_id: Optional[int] = Query(None),
    date_from: Optional[datetime] = Query(None),
    date_to: Optional[datetime] = Query(None),
    group_by: str = Query("month", pattern="^(day|week|month|year)$"),
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.role_required("admin", "business_owner", "specialist", "employee")),
    locale: str = Depends(deps.get_locale),
):
    """Get financial report."""
    from app.api.core.permissions import get_filtered_specialist_id, can_view_financials
    
    # Проверяем право на просмотр финансов
    if not can_view_financials(current_user, db):
        raise HTTPException(status_code=403, detail=_("not_allowed", locale))
    
    # Фильтрация по ролям
    if current_user.role == "admin":
        # Админ может видеть все, но может фильтровать по specialist_id
        pass
    elif current_user.role == "business_owner":
        # Владелец бизнеса видит финансовую отчетность всего бизнеса
        specialist_id = None
    else:
        # specialist и employee видят только свою финансовую информацию
        specialist_id = get_filtered_specialist_id(current_user)
    
    # Check cache
    cache_key = {
        'specialist_id': specialist_id,
        'date_from': date_from.isoformat() if date_from is not None else None,
        'date_to': date_to.isoformat() if date_to is not None else None,
        'group_by': group_by,
    }
    cached = get_cached_analytics('financial_report', **cache_key)
    if cached:
        return FinancialReportResponse(**cached)
    
    report = get_financial_report(
        db,
        specialist_id=specialist_id,
        date_from=date_from,
        date_to=date_to,
        group_by=group_by,
    )
    
    # Cache result
    cache_analytics('financial_report', report, ttl_seconds=600, **cache_key)
    
    return FinancialReportResponse(**report)


@router.get("/services/popular", response_model=PopularServicesResponse)
def get_popular_services_endpoint(
    specialist_id: Optional[int] = Query(None),
    date_from: Optional[datetime] = Query(None),
    date_to: Optional[datetime] = Query(None),
    limit: int = Query(10, ge=1, le=50),
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.role_required("admin", "business_owner", "specialist", "employee")),
    locale: str = Depends(deps.get_locale),
):
    """Get most popular services."""
    if current_user.role != "admin":
        specialist_id = current_user.id
    
    services = get_popular_services(
        db,
        specialist_id=specialist_id,
        date_from=date_from,
        date_to=date_to,
        limit=limit,
    )
    
    return PopularServicesResponse(services=services)


@router.get("/clients/statistics", response_model=ClientStatisticsResponse)
def get_clients_statistics(
    owner_id: Optional[int] = Query(None),
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.role_required("admin", "business_owner", "specialist", "employee")),
    locale: str = Depends(deps.get_locale),
):
    """Get client statistics."""
    if current_user.role != "admin":
        owner_id = current_user.id
    
    stats = get_client_statistics(db, owner_id=owner_id)
    
    return ClientStatisticsResponse(**stats)


@router.post("/reports/export")
def export_report(
    request: ReportExportRequest,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.role_required("admin", "business_owner", "specialist", "employee")),
    locale: str = Depends(deps.get_locale),
):
    """Export comprehensive report."""
    if current_user.role != "admin":
        request.specialist_id = current_user.id
    
    export_data = []
    filename = ""
    sheet_name = ""
    
    if request.report_type == "financial":
        report = get_financial_report(
            db,
            specialist_id=request.specialist_id,
            date_from=request.date_from,
            date_to=request.date_to,
            group_by="month",
        )
        
        # Prepare financial data for export
        export_data = [
            {
                'Period': period['period'],
                'Revenue': period['revenue'],
                'Visit Count': period['count'],
            }
            for period in report['revenue_by_period']
        ]
        
        # Add summary
        export_data.insert(0, {
            'Period': 'TOTAL',
            'Revenue': report['total_revenue'],
            'Visit Count': sum(p['count'] for p in report['revenue_by_period']),
        })
        
        filename = "financial_report"
        sheet_name = "Financial Report"
        
    elif request.report_type == "visits":
        stats = get_visit_statistics(
            db,
            specialist_id=request.specialist_id,
            date_from=request.date_from,
            date_to=request.date_to,
        )
        
        export_data = [
            {
                'Period': period['period'],
                'Visit Count': period['count'],
            }
            for period in stats['monthly_visits']
        ]
        
        export_data.insert(0, {
            'Period': 'TOTAL',
            'Visit Count': stats['total_visits'],
        })
        
        filename = "visits_report"
        sheet_name = "Visits Report"
        
    elif request.report_type == "services":
        services = get_popular_services(
            db,
            specialist_id=request.specialist_id,
            date_from=request.date_from,
            date_to=request.date_to,
            limit=50,
        )
        
        export_data = [
            {
                'Service Name': s['service_name'],
                'Visit Count': s['visit_count'],
                'Total Revenue': s['total_revenue'],
                'Average Price': s['average_price'],
            }
            for s in services
        ]
        
        filename = "services_report"
        sheet_name = "Services Report"
        
    elif request.report_type == "clients":
        stats = get_client_statistics(db, owner_id=request.specialist_id)
        
        export_data = [
            {
                'Client Name': client['client_name'],
                'Total Spending': client['spending'],
            }
            for client in stats['top_spenders']
        ]
        
        filename = "clients_report"
        sheet_name = "Clients Report"
        
    else:
        raise HTTPException(status_code=400, detail=_("invalid_report_type", locale))
    
    # Export based on format
    if request.format == "csv":
        return export_to_csv(export_data, filename=f"{filename}.csv")
    else:
        return export_to_excel(export_data, filename=f"{filename}.xlsx", sheet_name=sheet_name)

