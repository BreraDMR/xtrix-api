"""Analytics and reporting schemas."""
from datetime import datetime
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field


class VisitStatisticsResponse(BaseModel):
    """Response for visit statistics."""
    total_visits: int
    visits_by_status: Dict[str, int]
    monthly_visits: List[Dict[str, Any]]
    average_duration_minutes: Optional[float] = None


class FinancialReportResponse(BaseModel):
    """Response for financial report."""
    total_revenue: float
    average_revenue_per_visit: Optional[float] = None
    revenue_by_status: Dict[str, Dict[str, Any]]
    revenue_by_period: List[Dict[str, Any]]
    top_clients: List[Dict[str, Any]]


class PopularServicesResponse(BaseModel):
    """Response for popular services."""
    services: List[Dict[str, Any]]


class ClientStatisticsResponse(BaseModel):
    """Response for client statistics."""
    total_clients: int
    clients_by_gender: Dict[str, int]
    average_spending: Optional[float] = None
    top_spenders: List[Dict[str, Any]]


class AuditLogOut(BaseModel):
    """Audit log output schema."""
    id: int
    entity_type: str
    entity_id: int
    action: str
    user_id: Optional[int] = None
    user_name: Optional[str] = None
    changes: Optional[Dict[str, Any]] = None
    description: Optional[str] = None
    ip_address: Optional[str] = None
    user_agent: Optional[str] = None
    created_at: datetime

    class Config:
        from_attributes = True


class ReportExportRequest(BaseModel):
    """Request for exporting reports."""
    report_type: str = Field(..., description="Type of report: financial, visits, services, clients")
    format: str = Field("excel", description="Export format: csv or excel")
    date_from: Optional[datetime] = None
    date_to: Optional[datetime] = None
    specialist_id: Optional[int] = None
    include_charts: bool = Field(False, description="Include charts in Excel export")


# Advanced Analytics Schemas

class RetentionMetricsResponse(BaseModel):
    """Response for retention metrics."""
    retention_7_days: float
    retention_30_days: float
    retention_90_days: float
    repeat_rate: float
    average_days_between_visits: Optional[float] = None
    total_clients: int
    repeat_clients: int


class CohortData(BaseModel):
    """Single cohort data."""
    cohort_month: str
    total_clients: int
    month_0: Optional[float] = None
    month_1: Optional[float] = None
    month_2: Optional[float] = None
    month_3: Optional[float] = None
    month_4: Optional[float] = None
    month_5: Optional[float] = None
    month_6: Optional[float] = None
    month_7: Optional[float] = None
    month_8: Optional[float] = None
    month_9: Optional[float] = None
    month_10: Optional[float] = None
    month_11: Optional[float] = None


class CohortTableResponse(BaseModel):
    """Response for cohort retention table."""
    cohort_table: List[Dict[str, Any]]
    total_cohorts: int


class TrendsAnalysisResponse(BaseModel):
    """Response for trends analysis."""
    metric: str
    comparison_type: str
    period_label: str
    current_value: float
    previous_value: float
    change: float
    change_percent: float
    trend_direction: str  # 'up', 'down', 'stable'


class ForecastData(BaseModel):
    """Single forecast data point."""
    period: str
    forecasted_value: float


class ForecastResponse(BaseModel):
    """Response for forecasting."""
    forecast_type: str
    periods: int
    forecast: List[ForecastData]
    historical_average: float
    last_value: float


class ServiceAnalytics(BaseModel):
    """Service analytics data."""
    service_id: int
    service_name: str
    visit_count: int
    total_revenue: float
    average_price: float
    unique_clients: int
    ltv_per_client: float
    visits_per_client: float


class SpecialistAnalytics(BaseModel):
    """Specialist analytics data."""
    specialist_id: int
    specialist_name: str
    visit_count: int
    total_revenue: float
    average_check: float
    unique_clients: int
    revenue_per_client: float


class ServiceAndSpecialistAnalyticsResponse(BaseModel):
    """Response for service and specialist analytics."""
    services: List[ServiceAnalytics]
    specialists: List[SpecialistAnalytics]


class InsightData(BaseModel):
    """Single insight."""
    type: str  # 'info', 'warning', 'success'
    title: str
    message: str
    priority: str  # 'low', 'medium', 'high'


class SmartInsightsResponse(BaseModel):
    """Response for smart insights."""
    insights: List[InsightData]


class AnonymousBenchmarksResponse(BaseModel):
    """Response for anonymous benchmarks."""
    available: bool
    reason: Optional[str] = None
    business_type: Optional[str] = None
    business_count: Optional[int] = None
    average_check: Optional[float] = None
    average_visits_per_client: Optional[float] = None
    average_retention_rate: Optional[float] = None
    average_daily_load: Optional[float] = None


