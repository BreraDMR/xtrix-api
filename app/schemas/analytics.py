from typing import Literal, Optional, List, Dict, Any
from pydantic import ConfigDict, BaseModel, Field
from app.schemas.datetimes import UtcDatetime
from app.schemas.base import InputModel
class VisitStatisticsResponse(BaseModel):
    total_visits: int
    visits_by_status: Dict[str, int]
    monthly_visits: List[Dict[str, Any]]
    average_duration_minutes: Optional[float] = None
class FinancialReportResponse(BaseModel):
    total_revenue: float
    average_revenue_per_visit: Optional[float] = None
    revenue_by_status: Dict[str, Dict[str, Any]]
    revenue_by_period: List[Dict[str, Any]]
    top_clients: List[Dict[str, Any]]
class PopularServicesResponse(BaseModel):
    services: List[Dict[str, Any]]
class ClientStatisticsResponse(BaseModel):
    total_clients: int
    new_clients: int
    clients_by_gender: Dict[str, int]
    average_spending: Optional[float] = None
    top_spenders: List[Dict[str, Any]]
class PaymentMethodStats(BaseModel):
    total: float
    count: int
    percentage: float
class PaymentStatisticsResponse(BaseModel):
    total_payments: float
    total_count: int
    payments_by_method: Dict[str, PaymentMethodStats]
    card_payments: PaymentMethodStats
    cash_payments: PaymentMethodStats
    other_payments: PaymentMethodStats
class AuditLogOut(BaseModel):
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
    created_at: UtcDatetime
    model_config = ConfigDict(from_attributes=True)
class ReportExportRequest(InputModel):
    report_type: Literal["financial", "visits", "services", "clients"]
    format: Literal["csv", "excel"] = "excel"
    date_from: Optional[UtcDatetime] = None
    date_to: Optional[UtcDatetime] = None
    specialist_id: Optional[int] = None
    include_charts: bool = Field(False, description="Include charts in Excel export")
class RetentionMetricsResponse(BaseModel):
    retention_7_days: float
    retention_30_days: float
    retention_90_days: float
    repeat_rate: float
    average_days_between_visits: Optional[float] = None
    total_clients: int
    repeat_clients: int
class CohortData(BaseModel):
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
    cohort_table: List[Dict[str, Any]]
    total_cohorts: int
class TrendsAnalysisResponse(BaseModel):
    metric: str
    comparison_type: str
    period_label: str
    current_value: float
    previous_value: float
    change: float
    change_percent: float
    trend_direction: str  
class ForecastData(BaseModel):
    period: str
    forecasted_value: float
class ForecastResponse(BaseModel):
    forecast_type: str
    periods: int
    forecast: List[ForecastData]
    historical_average: float
    last_value: float
class ServiceAnalytics(BaseModel):
    service_id: int
    service_name: str
    visit_count: int
    total_revenue: float
    average_price: float
    unique_clients: int
    ltv_per_client: float
    visits_per_client: float
class SpecialistAnalytics(BaseModel):
    specialist_id: int
    specialist_name: str
    visit_count: int
    total_revenue: float
    average_check: float
    unique_clients: int
    revenue_per_client: float
class ServiceAndSpecialistAnalyticsResponse(BaseModel):
    services: List[ServiceAnalytics]
    specialists: List[SpecialistAnalytics]
class InsightData(BaseModel):
    type: str  
    title: str
    message: str
    priority: str  
class SmartInsightsResponse(BaseModel):
    insights: List[InsightData]
class AnonymousBenchmarksResponse(BaseModel):
    available: bool
    reason: Optional[str] = None
    business_type: Optional[str] = None
    business_count: Optional[int] = None
    average_check: Optional[float] = None
    average_visits_per_client: Optional[float] = None
    average_retention_rate: Optional[float] = None
    average_daily_load: Optional[float] = None
