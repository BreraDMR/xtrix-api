"""Enhanced Redis caching utilities."""
import json
from typing import Any, Optional, List, Dict
from datetime import datetime, timedelta
import hashlib

import redis

from app.api.core.config import settings


try:
    _redis = redis.Redis.from_url(settings.redis_url, decode_responses=True)
    _redis.ping()  # Test connection
    REDIS_AVAILABLE = True
except Exception:
    REDIS_AVAILABLE = False
    _redis = None


# Cache key prefixes
CACHE_PREFIX_CLIENT = "client"
CACHE_PREFIX_CLIENT_LIST = "clients:list"
CACHE_PREFIX_VISIT = "visit"
CACHE_PREFIX_VISIT_LIST = "visits:list"
CACHE_PREFIX_SERVICE = "service"
CACHE_PREFIX_SERVICE_LIST = "services:list"
CACHE_PREFIX_USER = "user"
CACHE_PREFIX_ANALYTICS = "analytics"


def _get_cache_key(prefix: str, *args) -> str:
    """Generate cache key from prefix and arguments."""
    key_parts = [prefix] + [str(arg) for arg in args]
    return ":".join(key_parts)


def _get_list_cache_key(prefix: str, filters: Optional[Dict[str, Any]] = None) -> str:
    """Generate cache key for list queries with filters."""
    if not filters:
        return f"{prefix}:all"
    
    # Create hash from filters for consistent key
    filter_str = json.dumps(filters, sort_keys=True)
    filter_hash = hashlib.md5(filter_str.encode()).hexdigest()[:8]
    return f"{prefix}:filter:{filter_hash}"


# Client caching
def get_client_cache_key(client_id: int) -> str:
    return _get_cache_key(CACHE_PREFIX_CLIENT, client_id)


def cache_client(client_id: int, data: dict, ttl_seconds: int = 300) -> None:
    """Cache client data."""
    if not REDIS_AVAILABLE:
        return
    try:
        _redis.setex(get_client_cache_key(client_id), ttl_seconds, json.dumps(data))
    except Exception:
        pass  # Fail silently if Redis is unavailable


def get_cached_client(client_id: int) -> Optional[dict]:
    """Get cached client data."""
    if not REDIS_AVAILABLE:
        return None
    try:
        raw = _redis.get(get_client_cache_key(client_id))
        if not raw:
            return None
        return json.loads(raw)
    except (json.JSONDecodeError, Exception):
        return None


def invalidate_client(client_id: int) -> None:
    """Invalidate client cache."""
    if not REDIS_AVAILABLE:
        return
    try:
        _redis.delete(get_client_cache_key(client_id))
        # Also invalidate client lists
        invalidate_client_list()
    except Exception:
        pass


# Client list caching
def get_client_list_cache_key(filters: Optional[Dict[str, Any]] = None) -> str:
    return _get_list_cache_key(CACHE_PREFIX_CLIENT_LIST, filters)


def cache_client_list(
    clients: List[dict],
    filters: Optional[Dict[str, Any]] = None,
    ttl_seconds: int = 300,
) -> None:
    """Cache client list."""
    if not REDIS_AVAILABLE:
        return
    try:
        key = get_client_list_cache_key(filters)
        _redis.setex(key, ttl_seconds, json.dumps(clients))
    except Exception:
        pass


def get_cached_client_list(filters: Optional[Dict[str, Any]] = None) -> Optional[List[dict]]:
    """Get cached client list."""
    if not REDIS_AVAILABLE:
        return None
    try:
        key = get_client_list_cache_key(filters)
        raw = _redis.get(key)
        if not raw:
            return None
        return json.loads(raw)
    except (json.JSONDecodeError, Exception):
        return None


def invalidate_client_list() -> None:
    """Invalidate all client list caches."""
    if not REDIS_AVAILABLE:
        return
    try:
        pattern = f"{CACHE_PREFIX_CLIENT_LIST}:*"
        keys = _redis.keys(pattern)
        if keys:
            _redis.delete(*keys)
    except Exception:
        pass


# Visit caching
def get_visit_cache_key(visit_id: int) -> str:
    return _get_cache_key(CACHE_PREFIX_VISIT, visit_id)


def cache_visit(visit_id: int, data: dict, ttl_seconds: int = 300) -> None:
    """Cache visit data."""
    if not REDIS_AVAILABLE:
        return
    try:
        _redis.setex(get_visit_cache_key(visit_id), ttl_seconds, json.dumps(data))
    except Exception:
        pass


def get_cached_visit(visit_id: int) -> Optional[dict]:
    """Get cached visit data."""
    if not REDIS_AVAILABLE:
        return None
    try:
        raw = _redis.get(get_visit_cache_key(visit_id))
        if not raw:
            return None
        return json.loads(raw)
    except (json.JSONDecodeError, Exception):
        return None


def invalidate_visit(visit_id: int) -> None:
    """Invalidate visit cache."""
    if not REDIS_AVAILABLE:
        return
    try:
        _redis.delete(get_visit_cache_key(visit_id))
        invalidate_visit_list()
    except Exception:
        pass


def invalidate_visit_list() -> None:
    """Invalidate all visit list caches."""
    if not REDIS_AVAILABLE:
        return
    try:
        pattern = f"{CACHE_PREFIX_VISIT_LIST}:*"
        keys = _redis.keys(pattern)
        if keys:
            _redis.delete(*keys)
    except Exception:
        pass


# Service caching
def get_service_cache_key(service_id: int) -> str:
    return _get_cache_key(CACHE_PREFIX_SERVICE, service_id)


def cache_service(service_id: int, data: dict, ttl_seconds: int = 300) -> None:
    """Cache service data."""
    if not REDIS_AVAILABLE:
        return
    try:
        _redis.setex(get_service_cache_key(service_id), ttl_seconds, json.dumps(data))
    except Exception:
        pass


def get_cached_service(service_id: int) -> Optional[dict]:
    """Get cached service data."""
    if not REDIS_AVAILABLE:
        return None
    try:
        raw = _redis.get(get_service_cache_key(service_id))
        if not raw:
            return None
        return json.loads(raw)
    except (json.JSONDecodeError, Exception):
        return None


def invalidate_service(service_id: int) -> None:
    """Invalidate service cache."""
    if not REDIS_AVAILABLE:
        return
    try:
        _redis.delete(get_service_cache_key(service_id))
        invalidate_service_list()
    except Exception:
        pass


def invalidate_service_list() -> None:
    """Invalidate all service list caches."""
    if not REDIS_AVAILABLE:
        return
    try:
        pattern = f"{CACHE_PREFIX_SERVICE_LIST}:*"
        keys = _redis.keys(pattern)
        if keys:
            _redis.delete(*keys)
    except Exception:
        pass


# Analytics caching
def get_analytics_cache_key(report_type: str, **filters) -> str:
    """Get cache key for analytics report."""
    filter_str = json.dumps(filters, sort_keys=True)
    filter_hash = hashlib.md5(filter_str.encode()).hexdigest()[:8]
    return f"{CACHE_PREFIX_ANALYTICS}:{report_type}:{filter_hash}"


def cache_analytics(report_type: str, data: dict, ttl_seconds: int = 600, **filters) -> None:
    """Cache analytics data."""
    if not REDIS_AVAILABLE:
        return
    try:
        key = get_analytics_cache_key(report_type, **filters)
        _redis.setex(key, ttl_seconds, json.dumps(data))
    except Exception:
        pass


def get_cached_analytics(report_type: str, **filters) -> Optional[dict]:
    """Get cached analytics data."""
    if not REDIS_AVAILABLE:
        return None
    try:
        key = get_analytics_cache_key(report_type, **filters)
        raw = _redis.get(key)
        if not raw:
            return None
        return json.loads(raw)
    except (json.JSONDecodeError, Exception):
        return None


def invalidate_analytics(report_type: Optional[str] = None) -> None:
    """Invalidate analytics cache."""
    if not REDIS_AVAILABLE:
        return
    try:
        if report_type:
            pattern = f"{CACHE_PREFIX_ANALYTICS}:{report_type}:*"
        else:
            pattern = f"{CACHE_PREFIX_ANALYTICS}:*"
        keys = _redis.keys(pattern)
        if keys:
            _redis.delete(*keys)
    except Exception:
        pass


# Generic cache utilities
def cache_data(key: str, data: Any, ttl_seconds: int = 300) -> None:
    """Generic cache function."""
    if not REDIS_AVAILABLE:
        return
    try:
        _redis.setex(key, ttl_seconds, json.dumps(data))
    except Exception:
        pass


def get_cached_data(key: str) -> Optional[Any]:
    """Generic get cached data function."""
    if not REDIS_AVAILABLE:
        return None
    try:
        raw = _redis.get(key)
        if not raw:
            return None
        return json.loads(raw)
    except (json.JSONDecodeError, Exception):
        return None


def invalidate_pattern(pattern: str) -> None:
    """Invalidate all keys matching pattern."""
    if not REDIS_AVAILABLE:
        return
    try:
        keys = _redis.keys(pattern)
        if keys:
            _redis.delete(*keys)
    except Exception:
        pass


def clear_all_cache() -> None:
    """Clear all cache (use with caution!)."""
    if not REDIS_AVAILABLE:
        return
    try:
        _redis.flushdb()
    except Exception:
        pass

