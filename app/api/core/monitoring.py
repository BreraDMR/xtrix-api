import time
import platform
from typing import Dict, Any
from sqlalchemy.orm import Session
from sqlalchemy import text
try:
    import psutil
    PSUTIL_AVAILABLE = True
except ImportError:
    PSUTIL_AVAILABLE = False
from app.api.core.config import settings
from app.db.session import SessionLocal
from app.api.core.clock import utcnow
class MetricsCollector:
    def __init__(self):
        self.request_count = 0
        self.error_count = 0
        self.response_times = []
        self.endpoint_stats: Dict[str, Dict[str, Any]] = {}
        self.status_code_counts: Dict[int, int] = {}
        self.start_time = utcnow()
    def record_request(self, method: str, path: str, status_code: int, duration: float):
        self.request_count += 1
        if status_code >= 400:
            self.error_count += 1
        if status_code not in self.status_code_counts:
            self.status_code_counts[status_code] = 0
        self.status_code_counts[status_code] += 1
        self.response_times.append(duration)
        if len(self.response_times) > 1000:
            self.response_times = self.response_times[-1000:]
        endpoint_key = f"{method} {path}"
        if endpoint_key not in self.endpoint_stats:
            self.endpoint_stats[endpoint_key] = {
                'count': 0,
                'errors': 0,
                'total_time': 0.0,
                'avg_time': 0.0,
                'min_time': float('inf'),
                'max_time': 0.0,
            }
        stats = self.endpoint_stats[endpoint_key]
        stats['count'] += 1
        if status_code >= 400:
            stats['errors'] += 1
        stats['total_time'] += duration
        stats['avg_time'] = stats['total_time'] / stats['count']
        stats['min_time'] = min(stats['min_time'], duration)
        stats['max_time'] = max(stats['max_time'], duration)
    def get_metrics(self) -> Dict[str, Any]:
        avg_response_time = (
            sum(self.response_times) / len(self.response_times)
            if self.response_times else 0.0
        )
        min_response_time = min(self.response_times) if self.response_times else 0.0
        max_response_time = max(self.response_times) if self.response_times else 0.0
        uptime = (utcnow() - self.start_time).total_seconds()
        return {
            'request_count': self.request_count,
            'error_count': self.error_count,
            'error_rate': self.error_count / self.request_count if self.request_count > 0 else 0.0,
            'avg_response_time': avg_response_time,
            'min_response_time': min_response_time,
            'max_response_time': max_response_time,
            'response_times': self.response_times,  
            'status_code_counts': self.status_code_counts,
            'endpoint_stats': self.endpoint_stats,
            'uptime_seconds': uptime,
        }
    def reset(self):
        self.request_count = 0
        self.error_count = 0
        self.response_times = []
        self.endpoint_stats = {}
        self.status_code_counts = {}
        self.start_time = utcnow()
metrics_collector = MetricsCollector()
def check_database_health(db: Session) -> Dict[str, Any]:
    try:
        start_time = time.time()
        result = db.execute(text("SELECT 1"))
        result.fetchone()
        response_time = (time.time() - start_time) * 1000  
        try:
            version_result = db.execute(text("SELECT version()"))
            db_version = version_result.fetchone()[0] if version_result else "unknown"
        except:
            db_version = "unknown"
        pool_info = {}
        try:
            pool = db.bind.pool
            pool_info = {
                'size': pool.size(),
                'checked_in': pool.checkedin(),
                'checked_out': pool.checkedout(),
                'overflow': pool.overflow(),
            }
        except:
            pass
        return {
            'status': 'healthy',
            'response_time_ms': round(response_time, 2),
            'version': db_version,
            'pool': pool_info,
        }
    except Exception as e:
        return {
            'status': 'unhealthy',
            'error': str(e),
            'error_type': type(e).__name__,
        }
def check_redis_health() -> Dict[str, Any]:
    try:
        try:
            import redis
            from app.api.core.config import settings
            if settings.redis_url.startswith("redis://"):
                r = redis.from_url(settings.redis_url)
                r.ping()
                return {'status': 'healthy'}
        except ImportError:
            pass
        except Exception:
            pass
        return {'status': 'not_configured'}
    except Exception as e:
        return {
            'status': 'unhealthy',
            'error': str(e),
        }
def get_system_metrics() -> Dict[str, Any]:
    try:
        cpu_percent = psutil.cpu_percent(interval=0.1)
        memory = psutil.virtual_memory()
        disk = psutil.disk_usage('/')
        return {
            'cpu': {
                'percent': cpu_percent,
                'count': psutil.cpu_count(),
            },
            'memory': {
                'total_gb': round(memory.total / (1024**3), 2),
                'available_gb': round(memory.available / (1024**3), 2),
                'used_gb': round(memory.used / (1024**3), 2),
                'percent': memory.percent,
            },
            'disk': {
                'total_gb': round(disk.total / (1024**3), 2),
                'used_gb': round(disk.used / (1024**3), 2),
                'free_gb': round(disk.free / (1024**3), 2),
                'percent': disk.percent,
            },
        }
    except Exception as e:
        return {
            'error': f"Failed to get system metrics: {str(e)}",
        }
async def get_health_status() -> Dict[str, Any]:
    db = SessionLocal()
    try:
        db_health = check_database_health(db)
        redis_health = check_redis_health()
        system_metrics = get_system_metrics()
        overall_status = 'healthy'
        if db_health.get('status') != 'healthy':
            overall_status = 'degraded'
        if 'memory' in system_metrics and system_metrics['memory'].get('percent', 0) > 90:
            overall_status = 'degraded'
        if 'disk' in system_metrics and system_metrics['disk'].get('percent', 0) > 90:
            overall_status = 'degraded'
        return {
            'status': overall_status,
            'timestamp': utcnow().isoformat(),
            'database': db_health,
            'redis': redis_health,
            'system': system_metrics,
            'version': '1.0.0',
            'environment': settings.environment,
            'platform': {
                'system': platform.system(),
                'release': platform.release(),
                'machine': platform.machine(),
            },
        }
    finally:
        db.close()
