"""Write and read the business-scoped action journal.

The journal is part of the caller's transaction: it never commits on its own, so a rolled
back action cannot leave a "success" row behind. Journal failures are isolated in a
savepoint and never abort the business action.

Only explicitly catalogued fields are stored. Tokens, passwords, notes, raw assistant
context, medical answers and contact details are never copied into the journal.
"""
from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Mapping, Optional

from sqlalchemy.orm import Session

from app.api.core.clock import utcnow
from app.api.core.logger import get_logger
from app.db.models.business_audit_event import BusinessAuditEvent

logger = get_logger("audit")

MAX_CHANGE_TEXT = 200
MAX_FIELD_NAME = 40
_SENSITIVE_PART = re.compile(
    r"(password|passwd|secret|token|signature|authoriz|cookie|api[_-]?key|private[_-]?key|"
    r"medical|allerg|chronic|pregnan|viral|diagnos|answer|note|email|phone|contact|birth)",
    re.IGNORECASE,
)
_FIELD_NAME = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
_REQUEST_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}$")


@dataclass(frozen=True)
class EventSpec:
    entity_type: str
    value_keys: frozenset[str]
    name_keys: frozenset[str] = frozenset()


VISIT_VALUE_KEYS = frozenset(
    {
        "status",
        "date_start",
        "date_end",
        "service_id",
        "specialist_id",
        "price",
        "payment_method",
        "items_count",
    }
)
VISIT_NAME_KEYS = frozenset(
    {
        "status",
        "date_start",
        "date_end",
        "service_id",
        "specialist_id",
        "price",
        "payment_method",
        "service_name",
        "note",
        "photo_url",
        "items",
    }
)
CLIENT_NAME_KEYS = frozenset(
    {
        "full_name",
        "birth_date",
        "gender",
        "contact_phone",
        "contact_email",
        "location_lat",
        "location_lon",
        "language",
    }
)

EVENT_CATALOG: dict[str, EventSpec] = {
    "visit.created": EventSpec("visit", VISIT_VALUE_KEYS, VISIT_NAME_KEYS),
    "visit.updated": EventSpec("visit", VISIT_VALUE_KEYS, VISIT_NAME_KEYS),
    "visit.moved": EventSpec("visit", VISIT_VALUE_KEYS, VISIT_NAME_KEYS),
    "visit.cancelled": EventSpec("visit", VISIT_VALUE_KEYS, VISIT_NAME_KEYS),
    "payment.paid": EventSpec(
        "payment",
        frozenset({"amount", "currency", "status", "provider", "provider_event_id"}),
    ),
    "payment.failed": EventSpec(
        "payment",
        frozenset({"amount", "currency", "status", "provider", "provider_event_id"}),
    ),
    "payment.refunded": EventSpec(
        "payment",
        frozenset({"amount", "currency", "status", "refund_id", "provider"}),
    ),
    "questionnaire.submitted": EventSpec(
        "questionnaire", frozenset({"version", "fields_count", "answered_count"})
    ),
    "client.updated": EventSpec("client", frozenset(), CLIENT_NAME_KEYS),
}


def _sensitive_key(key: str) -> bool:
    return bool(_SENSITIVE_PART.search(key))


for _action, _spec in EVENT_CATALOG.items():
    # The catalog is the first line of redaction: a sensitive value key must never enter it.
    # Aggregated counts (answered_count, fields_count) are not confidential content.
    assert not any(
        _sensitive_key(key) and not key.endswith("_count") for key in _spec.value_keys
    ), _action


def _clean_scalar(value: Any) -> Any:
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value if abs(value) <= 10**12 else None
    if isinstance(value, float):
        return value if value == value and abs(value) <= 10**12 else None
    if isinstance(value, Decimal):
        return format(value, "f")[:MAX_CHANGE_TEXT]
    if isinstance(value, datetime):
        return value.isoformat() + "Z" if value.tzinfo is None else value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        text = "".join(ch for ch in text if ch.isprintable() or ch in "\t\n")
        return text[:MAX_CHANGE_TEXT]
    return None


def _clean_value(value: Any) -> Any:
    # A diff is either a scalar or {"old": ..., "new": ...}; arbitrary structures are dropped
    # so a caller cannot smuggle raw answers or assistant context through this writer.
    if isinstance(value, Mapping):
        if not set(value).issubset({"old", "new"}):
            return None
        cleaned = {
            key: _clean_scalar(value.get(key))
            for key in ("old", "new")
            if key in value
        }
        return cleaned or None
    return _clean_scalar(value)


def sanitize_changes(action: str, changes: Optional[Mapping[str, Any]]) -> Optional[dict[str, Any]]:
    """Keep only the diff fields allowed for this action."""
    spec = EVENT_CATALOG.get(action)
    if spec is None or not isinstance(changes, Mapping):
        return None
    result: dict[str, Any] = {}
    fields = changes.get("fields")
    if isinstance(fields, (list, tuple)):
        names = []
        for name in fields:
            if (
                isinstance(name, str)
                and _FIELD_NAME.match(name)
                and name in spec.name_keys
                and name not in names
            ):
                names.append(name)
        if names:
            result["fields"] = names[:MAX_FIELD_NAME]
    for key in spec.value_keys:
        if key not in changes:
            continue
        value = _clean_value(changes[key])
        if value is not None:
            result[key] = value
    return result or None


def clean_request_id(value: Optional[str], fallback: Optional[str] = None) -> str:
    """Return a bounded correlation id; arbitrary request headers never reach storage raw."""
    for candidate in (value, fallback):
        if not candidate:
            continue
        text = str(candidate).strip()
        if _REQUEST_ID.match(text):
            return text[:64]
        text = re.sub(r"[^A-Za-z0-9_.:-]", "_", text)[:64]
        if text:
            return text
    return uuid.uuid4().hex


def request_id_for(request) -> str:
    value = request.headers.get("X-Request-ID") if request is not None else None
    return clean_request_id(value)


def actor_snapshot(actor) -> tuple[Optional[int], Optional[str], Optional[str]]:
    if actor is None:
        return None, None, None
    name = _clean_scalar(getattr(actor, "full_name", None))
    role = _clean_scalar(getattr(actor, "role", None))
    return getattr(actor, "id", None), name, role


def record_event(
    db: Session,
    *,
    action: str,
    entity_type: str,
    entity_id: int,
    business_id: Optional[int] = None,
    owner_user_id: Optional[int] = None,
    changes: Optional[Mapping[str, Any]] = None,
    actor=None,
    actor_user_id: Optional[int] = None,
    actor_name: Optional[str] = None,
    actor_role: Optional[str] = None,
    request_id: Optional[str] = None,
) -> Optional[BusinessAuditEvent]:
    """Append one journal row inside the caller's transaction, without committing.

    A journal problem is contained in a savepoint: the business action can still commit,
    and the missing row is logged. An action rollback also rolls the row back because
    there is no separate commit here.
    """
    spec = EVENT_CATALOG.get(action)
    if spec is None or spec.entity_type != entity_type:
        logger.error("audit: unknown event %s/%s", entity_type, action)
        return None
    if business_id is not None:
        owner_user_id = None
    elif owner_user_id is None:
        logger.warning("audit: event %s has no tenant scope; skipped", action)
        return None

    try:
        # Flush the action first: its own validation failure must remain the caller's error,
        # not be mistaken for a journal failure and swallowed below.
        db.flush()
    except Exception:
        raise

    savepoint = None
    event = None
    try:
        savepoint = db.begin_nested()
        snapshot_id, snapshot_name, snapshot_role = actor_snapshot(actor)
        if actor_user_id is not None:
            snapshot_id = actor_user_id
        if actor_name is not None:
            snapshot_name = _clean_scalar(actor_name)
        if actor_role is not None:
            snapshot_role = _clean_scalar(actor_role)
        event = BusinessAuditEvent(
            business_id=business_id,
            owner_user_id=owner_user_id,
            actor_user_id=snapshot_id,
            actor_name=snapshot_name,
            actor_role=snapshot_role,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            request_id=clean_request_id(request_id),
            changes=sanitize_changes(action, changes),
            created_at=utcnow(),
        )
        db.add(event)
        db.flush([event])
        savepoint.commit()
        return event
    except Exception:
        try:
            if savepoint is not None:
                savepoint.rollback()
            if event is not None:
                db.expunge(event)
        except Exception:
            pass
        logger.exception("audit: could not record %s for %s %s", action, entity_type, entity_id)
        return None


@dataclass(frozen=True)
class AuditScope:
    business_id: Optional[int] = None
    owner_user_id: Optional[int] = None


def list_events(
    db: Session,
    scope: AuditScope,
    *,
    action: Optional[str] = None,
    entity_type: Optional[str] = None,
    actor_user_id: Optional[int] = None,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    exclude_action_prefixes: tuple[str, ...] = (),
    limit: int = 20,
    offset: int = 0,
) -> tuple[list[BusinessAuditEvent], int]:
    query = db.query(BusinessAuditEvent)
    if scope.business_id is not None:
        query = query.filter(BusinessAuditEvent.business_id == scope.business_id)
    else:
        # Solo events are scoped by their owner, not by a nullable business column.
        query = query.filter(BusinessAuditEvent.owner_user_id == scope.owner_user_id)
    for prefix in exclude_action_prefixes:
        query = query.filter(~BusinessAuditEvent.action.startswith(prefix))
    if action:
        query = query.filter(BusinessAuditEvent.action == action)
    if entity_type:
        query = query.filter(BusinessAuditEvent.entity_type == entity_type)
    if actor_user_id is not None:
        query = query.filter(BusinessAuditEvent.actor_user_id == actor_user_id)
    if date_from is not None:
        query = query.filter(BusinessAuditEvent.created_at >= date_from)
    if date_to is not None:
        query = query.filter(BusinessAuditEvent.created_at <= date_to)
    total = query.count()
    rows = (
        query.order_by(BusinessAuditEvent.created_at.desc(), BusinessAuditEvent.id.desc())
        .offset(max(0, offset))
        .limit(max(1, min(limit, 10_000)))
        .all()
    )
    return rows, total
