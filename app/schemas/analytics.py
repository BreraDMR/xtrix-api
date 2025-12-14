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


