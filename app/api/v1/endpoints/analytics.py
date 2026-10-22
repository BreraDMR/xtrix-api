from datetime import datetime
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session
from app.api import deps
from app.schemas.datetimes import UtcDatetime
from app.api.core.i18n import _
from app.api.core.analytics import (
    get_visit_statistics,
    get_financial_report,
    get_popular_services,
    get_client_statistics,
    get_payment_statistics,
)
from app.api.core.advanced_analytics import (
    get_retention_metrics,
    get_client_cohorts,
    get_trends_analysis,
    get_forecast,
    get_service_and_specialist_analytics,
    get_smart_insights,
    get_anonymous_benchmarks,
)
from app.api.core.redis_client import (
    cache_analytics, get_cached_analytics,
)
from app.api.core.export import export_to_csv, export_to_excel
from app.db.models.user import User
from app.schemas.analytics import (
    VisitStatisticsResponse,
    FinancialReportResponse,
    PopularServicesResponse,
    ClientStatisticsResponse,
    PaymentStatisticsResponse,
    ReportExportRequest,
    RetentionMetricsResponse,
    CohortTableResponse,
    TrendsAnalysisResponse,
    ForecastResponse,
    ServiceAndSpecialistAnalyticsResponse,
    SmartInsightsResponse,
    AnonymousBenchmarksResponse,
)
from app.api.core.permissions import get_user_business_id
from app.api.middleware.rate_limit import limiter, EXPORT_LIMIT
router = APIRouter()
def _scope(current_user: User, db: Session, requested_specialist_id: Optional[int] = None):
    """(specialist_id, business_id) whose numbers the caller may see.

    Platform admin: anything. Business owner: their business (or one of its people).
    Everyone else: only their own numbers, inside their business if they have one.
    """
    if current_user.role == "admin":
        return requested_specialist_id, None
    business_id = get_user_business_id(current_user, db)
    if business_id and current_user.role == "business_owner":
        from app.api.core.permissions import check_specialist_in_scope
        if requested_specialist_id:
            if not check_specialist_in_scope(db, current_user, requested_specialist_id):
                # somebody else's person - say no instead of quietly showing the whole business
                raise HTTPException(status_code=403, detail=_("not_allowed"))
            return requested_specialist_id, business_id
        return None, business_id
    return current_user.id, business_id
@router.get("/visits/statistics", response_model=VisitStatisticsResponse)
def get_visits_statistics(
    specialist_id: Optional[int] = Query(None),
    client_id: Optional[int] = Query(None),
    date_from: Optional[UtcDatetime] = Query(None),
    date_to: Optional[UtcDatetime] = Query(None),
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.role_required("admin", "business_owner", "specialist", "employee")),
    locale: str = Depends(deps.get_locale),
):
    specialist_id, business_id = _scope(current_user, db, specialist_id)
    cache_key = {
        'specialist_id': specialist_id,
        'business_id': business_id,
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
        business_id=business_id,
        client_id=client_id,
        date_from=date_from,
        date_to=date_to,
    )
    cache_analytics('visits_statistics', stats, ttl_seconds=300, **cache_key)
    return VisitStatisticsResponse(**stats)
@router.get("/financial/report", response_model=FinancialReportResponse)
def get_financial_report_endpoint(
    specialist_id: Optional[int] = Query(None),
    date_from: Optional[UtcDatetime] = Query(None),
    date_to: Optional[UtcDatetime] = Query(None),
    group_by: str = Query("month", pattern="^(day|week|month|year)$"),
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.role_required("admin", "business_owner", "specialist", "employee")),
    locale: str = Depends(deps.get_locale),
):
    from app.api.core.permissions import can_view_financials
    if not can_view_financials(current_user, db):
        raise HTTPException(status_code=403, detail=_("not_allowed", locale))
    specialist_id, business_id = _scope(current_user, db, specialist_id)
    cache_key = {
        'specialist_id': specialist_id,
        'business_id': business_id,
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
        business_id=business_id,
        date_from=date_from,
        date_to=date_to,
        group_by=group_by,
    )
    cache_analytics('financial_report', report, ttl_seconds=600, **cache_key)
    return FinancialReportResponse(**report)
@router.get("/services/popular", response_model=PopularServicesResponse)
def get_popular_services_endpoint(
    specialist_id: Optional[int] = Query(None),
    date_from: Optional[UtcDatetime] = Query(None),
    date_to: Optional[UtcDatetime] = Query(None),
    limit: int = Query(10, ge=1, le=50),
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.role_required("admin", "business_owner", "specialist", "employee")),
    locale: str = Depends(deps.get_locale),
):
    specialist_id, business_id = _scope(current_user, db, specialist_id)
    services = get_popular_services(
        db,
        specialist_id=specialist_id,
        business_id=business_id,
        date_from=date_from,
        date_to=date_to,
        limit=limit,
    )
    return PopularServicesResponse(services=services)
@router.get("/clients/statistics", response_model=ClientStatisticsResponse)
def get_clients_statistics(
    owner_id: Optional[int] = Query(None),
    business_id: Optional[int] = Query(None),
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.role_required("admin", "business_owner", "specialist", "employee")),
    locale: str = Depends(deps.get_locale),
):
    user_business_id = get_user_business_id(current_user, db)
    if user_business_id:
        business_id = user_business_id
        owner_id = None  
    elif current_user.role != "admin":
        owner_id = current_user.id
    stats = get_client_statistics(db, owner_id=owner_id, business_id=business_id)
    return ClientStatisticsResponse(**stats)
@router.get("/payments/statistics", response_model=PaymentStatisticsResponse)
def get_payments_statistics(
    specialist_id: Optional[int] = Query(None),
    date_from: Optional[UtcDatetime] = Query(None),
    date_to: Optional[UtcDatetime] = Query(None),
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.role_required("admin", "business_owner", "specialist", "employee")),
    locale: str = Depends(deps.get_locale),
):
    specialist_id, business_id = _scope(current_user, db, specialist_id)
    stats = get_payment_statistics(
        db,
        specialist_id=specialist_id,
        business_id=business_id,
        date_from=date_from,
        date_to=date_to,
    )
    return PaymentStatisticsResponse(**stats)
@router.post("/reports/export")
@limiter.limit(EXPORT_LIMIT)
def export_report(
    request: Request,
    payload: ReportExportRequest,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.role_required("admin", "business_owner", "specialist", "employee")),
    locale: str = Depends(deps.get_locale),
):
    payload.specialist_id, business_id = _scope(current_user, db, payload.specialist_id)
    export_data = []
    filename = ""
    sheet_name = ""
    if payload.report_type == "financial":
        report = get_financial_report(
            db,
            specialist_id=payload.specialist_id,
            business_id=business_id,
            date_from=payload.date_from,
            date_to=payload.date_to,
            group_by="month",
        )
        export_data = [
            {
                'Period': period['period'],
                'Revenue': period['revenue'],
                'Visit Count': period['count'],
            }
            for period in report['revenue_by_period']
        ]
        export_data.insert(0, {
            'Period': 'TOTAL',
            'Revenue': report['total_revenue'],
            'Visit Count': sum(p['count'] for p in report['revenue_by_period']),
        })
        filename = "financial_report"
        sheet_name = "Financial Report"
    elif payload.report_type == "visits":
        stats = get_visit_statistics(
            db,
            specialist_id=payload.specialist_id,
            business_id=business_id,
            date_from=payload.date_from,
            date_to=payload.date_to,
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
    elif payload.report_type == "services":
        services = get_popular_services(
            db,
            specialist_id=payload.specialist_id,
            business_id=business_id,
            date_from=payload.date_from,
            date_to=payload.date_to,
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
    elif payload.report_type == "clients":
        stats = get_client_statistics(
            db, owner_id=None if business_id else payload.specialist_id, business_id=business_id
        )
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
    if payload.format == "csv":
        return export_to_csv(export_data, filename=f"{filename}.csv")
    else:
        return export_to_excel(export_data, filename=f"{filename}.xlsx", sheet_name=sheet_name)
@router.get("/retention/metrics", response_model=RetentionMetricsResponse)
def get_retention_metrics_endpoint(
    date_from: Optional[UtcDatetime] = Query(None),
    date_to: Optional[UtcDatetime] = Query(None),
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.role_required("admin", "business_owner", "specialist", "employee")),
    locale: str = Depends(deps.get_locale),
):
    specialist_id, business_id = _scope(current_user, db)
    metrics = get_retention_metrics(db, business_id=business_id, specialist_id=specialist_id, date_from=date_from, date_to=date_to)
    return RetentionMetricsResponse(**metrics)
@router.get("/retention/cohorts", response_model=CohortTableResponse)
def get_cohorts_endpoint(
    date_from: Optional[UtcDatetime] = Query(None),
    date_to: Optional[UtcDatetime] = Query(None),
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.role_required("admin", "business_owner")),
    locale: str = Depends(deps.get_locale),
):
    specialist_id, business_id = _scope(current_user, db)
    cohorts = get_client_cohorts(db, business_id=business_id, specialist_id=specialist_id, date_from=date_from, date_to=date_to)
    return CohortTableResponse(**cohorts)
@router.get("/trends", response_model=TrendsAnalysisResponse)
def get_trends_endpoint(
    metric: str = Query("revenue", pattern="^(revenue|clients|visits|average_check|load)$"),
    comparison_type: str = Query("MoM", pattern="^(MoM|WoW|YoY)$"),
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.role_required("admin", "business_owner", "specialist", "employee")),
    locale: str = Depends(deps.get_locale),
):
    specialist_id, business_id = _scope(current_user, db)
    trends = get_trends_analysis(db, business_id=business_id, specialist_id=specialist_id, metric=metric, comparison_type=comparison_type)
    return TrendsAnalysisResponse(**trends)
@router.get("/forecast", response_model=ForecastResponse)
def get_forecast_endpoint(
    forecast_type: str = Query("revenue", pattern="^(revenue|visits)$"),
    periods: int = Query(1, ge=1, le=12),
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.role_required("admin", "business_owner")),
    locale: str = Depends(deps.get_locale),
):
    specialist_id, business_id = _scope(current_user, db)
    forecast = get_forecast(db, business_id=business_id, specialist_id=specialist_id, forecast_type=forecast_type, periods=periods)
    return ForecastResponse(**forecast)
@router.get("/services-specialists", response_model=ServiceAndSpecialistAnalyticsResponse)
def get_service_and_specialist_analytics_endpoint(
    date_from: Optional[UtcDatetime] = Query(None),
    date_to: Optional[UtcDatetime] = Query(None),
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.role_required("admin", "business_owner")),
    locale: str = Depends(deps.get_locale),
):
    specialist_id, business_id = _scope(current_user, db)
    analytics = get_service_and_specialist_analytics(db, business_id=business_id, specialist_id=specialist_id, date_from=date_from, date_to=date_to
    )
    return ServiceAndSpecialistAnalyticsResponse(**analytics)
@router.get("/insights", response_model=SmartInsightsResponse)
def get_insights_endpoint(
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.role_required("admin", "business_owner")),
    locale: str = Depends(deps.get_locale),
):
    specialist_id, business_id = _scope(current_user, db)
    insights = get_smart_insights(db, business_id=business_id, specialist_id=specialist_id, locale=locale)
    return SmartInsightsResponse(insights=insights)
@router.get("/benchmarks", response_model=AnonymousBenchmarksResponse)
def get_benchmarks_endpoint(
    business_type: Optional[str] = Query(None, description="Filter by business type"),
    min_businesses: int = Query(10, ge=5, le=50),
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.role_required("admin", "business_owner")),
    locale: str = Depends(deps.get_locale),
):
    benchmarks = get_anonymous_benchmarks(db, business_type=business_type, min_businesses=min_businesses, locale=locale)
    return AnonymousBenchmarksResponse(**benchmarks)
