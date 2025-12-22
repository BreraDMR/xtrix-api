from datetime import datetime
from fastapi import APIRouter, Depends, Response
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.api import deps
from app.api.core.monitoring import metrics_collector, get_health_status
from app.db.models.visit import Visit

router = APIRouter()


@router.get("/health")
async def health_check():
    """Health check endpoint."""
    return await get_health_status()


@router.get("/health/live")
async def liveness():
    """Liveness probe - simple check if service is running."""
    return {"status": "alive"}


@router.get("/health/ready")
async def readiness(db: Session = Depends(deps.get_db)):
    """Readiness probe - check if service is ready to serve requests."""
    try:
        db.execute(func.select(1))
        return {"status": "ready"}
    except Exception:
        return Response(status_code=503, content='{"status": "not_ready"}')


@router.get("/metrics")
def get_metrics(_=Depends(deps.role_required("admin"))):
    """Get application metrics (admin only)."""
    return metrics_collector.get_metrics()


@router.get("/metrics/prometheus")
def prometheus_metrics(_=Depends(deps.role_required("admin"))):
    """Get Prometheus format metrics with enhanced details."""
    from app.api.core.monitoring import get_system_metrics
    
    metrics = metrics_collector.get_metrics()
    system_metrics = get_system_metrics()
    
    # Format as Prometheus metrics
    lines = []
    
    # HTTP Request metrics
    lines.append("# HELP http_requests_total Total number of HTTP requests")
    lines.append("# TYPE http_requests_total counter")
    lines.append(f"http_requests_total {metrics['request_count']}")
    
    lines.append("# HELP http_errors_total Total number of HTTP errors")
    lines.append("# TYPE http_errors_total counter")
    lines.append(f"http_errors_total {metrics['error_count']}")
    
    lines.append("# HELP http_request_duration_seconds Request duration in seconds")
    lines.append("# TYPE http_request_duration_seconds summary")
    response_times = metrics.get('response_times', [])
    if response_times:
        lines.append(f"http_request_duration_seconds_sum {sum(response_times)}")
        lines.append(f"http_request_duration_seconds_count {len(response_times)}")
        lines.append(f"http_request_duration_seconds_avg {metrics.get('avg_response_time', 0)}")
        lines.append(f"http_request_duration_seconds_min {metrics.get('min_response_time', 0)}")
        lines.append(f"http_request_duration_seconds_max {metrics.get('max_response_time', 0)}")
    else:
        lines.append("http_request_duration_seconds_sum 0")
        lines.append("http_request_duration_seconds_count 0")
        lines.append("http_request_duration_seconds_avg 0")
        lines.append("http_request_duration_seconds_min 0")
        lines.append("http_request_duration_seconds_max 0")
    
    lines.append("# HELP http_error_rate Error rate (0-1)")
    lines.append("# TYPE http_error_rate gauge")
    lines.append(f"http_error_rate {metrics.get('error_rate', 0)}")
    
    # Status code metrics
    lines.append("# HELP http_status_codes_total Total requests by status code")
    lines.append("# TYPE http_status_codes_total counter")
    for status_code, count in metrics.get('status_code_counts', {}).items():
        lines.append(f'http_status_codes_total{{code="{status_code}"}} {count}')
    
    # Endpoint stats
    lines.append("# HELP http_endpoint_requests_total Total requests per endpoint")
    lines.append("# TYPE http_endpoint_requests_total counter")
    lines.append("# HELP http_endpoint_errors_total Total errors per endpoint")
    lines.append("# TYPE http_endpoint_errors_total counter")
    lines.append("# HELP http_endpoint_duration_seconds Request duration per endpoint")
    lines.append("# TYPE http_endpoint_duration_seconds summary")
    
    for endpoint, stats in metrics.get('endpoint_stats', {}).items():
        method, path = endpoint.split(' ', 1) if ' ' in endpoint else (endpoint, '')
        # Escape special characters in path for Prometheus
        path_escaped = path.replace('"', '\\"').replace('\n', '\\n').replace('\\', '\\\\')
        
        lines.append(f'http_endpoint_requests_total{{method="{method}",path="{path_escaped}"}} {stats.get("count", 0)}')
        lines.append(f'http_endpoint_errors_total{{method="{method}",path="{path_escaped}"}} {stats.get("errors", 0)}')
        lines.append(f'http_endpoint_duration_seconds_avg{{method="{method}",path="{path_escaped}"}} {stats.get("avg_time", 0)}')
        lines.append(f'http_endpoint_duration_seconds_min{{method="{method}",path="{path_escaped}"}} {stats.get("min_time", 0)}')
        lines.append(f'http_endpoint_duration_seconds_max{{method="{method}",path="{path_escaped}"}} {stats.get("max_time", 0)}')
    
    # System metrics (only if psutil is available)
    if 'error' not in system_metrics:
        if 'cpu' in system_metrics:
            lines.append("# HELP system_cpu_percent CPU usage percentage")
            lines.append("# TYPE system_cpu_percent gauge")
            lines.append(f"system_cpu_percent {system_metrics['cpu'].get('percent', 0)}")
            lines.append("# HELP system_cpu_cores Number of CPU cores")
            lines.append("# TYPE system_cpu_cores gauge")
            lines.append(f"system_cpu_cores {system_metrics['cpu'].get('count', 0)}")
        
        if 'memory' in system_metrics:
            lines.append("# HELP system_memory_total_bytes Total memory in bytes")
            lines.append("# TYPE system_memory_total_bytes gauge")
            lines.append(f"system_memory_total_bytes {int(system_metrics['memory'].get('total_gb', 0) * 1024**3)}")
            lines.append("# HELP system_memory_used_bytes Used memory in bytes")
            lines.append("# TYPE system_memory_used_bytes gauge")
            lines.append(f"system_memory_used_bytes {int(system_metrics['memory'].get('used_gb', 0) * 1024**3)}")
            lines.append("# HELP system_memory_percent Memory usage percentage")
            lines.append("# TYPE system_memory_percent gauge")
            lines.append(f"system_memory_percent {system_metrics['memory'].get('percent', 0)}")
        
        if 'disk' in system_metrics:
            lines.append("# HELP system_disk_total_bytes Total disk space in bytes")
            lines.append("# TYPE system_disk_total_bytes gauge")
            lines.append(f"system_disk_total_bytes {int(system_metrics['disk'].get('total_gb', 0) * 1024**3)}")
            lines.append("# HELP system_disk_used_bytes Used disk space in bytes")
            lines.append("# TYPE system_disk_used_bytes gauge")
            lines.append(f"system_disk_used_bytes {int(system_metrics['disk'].get('used_gb', 0) * 1024**3)}")
            lines.append("# HELP system_disk_percent Disk usage percentage")
            lines.append("# TYPE system_disk_percent gauge")
            lines.append(f"system_disk_percent {system_metrics['disk'].get('percent', 0)}")
    
    # Uptime
    lines.append("# HELP system_uptime_seconds System uptime in seconds")
    lines.append("# TYPE system_uptime_seconds gauge")
    lines.append(f"system_uptime_seconds {metrics.get('uptime_seconds', 0)}")
    
    return Response(content="\n".join(lines), media_type="text/plain")


@router.get("/revenue")
def revenue(
    start: datetime,
    end: datetime,
    db: Session = Depends(deps.get_db),
    _=Depends(deps.role_required("admin", "specialist")),
):
    res = (
        db.query(func.sum(Visit.price))
        .filter(Visit.date_start >= start, Visit.date_start <= end)
        .scalar()
    )
    return {"revenue": res or 0.0}
