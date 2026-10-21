"""Deposit policy, provider events and the paid/held booking transition.

Money always comes from the stored visit total and the stored attempt, never from the
browser. The attempt row is the only place that decides whether a slot is held, and
every provider event is applied while that row is locked.
"""
import hashlib
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Optional

from sqlalchemy.orm import Session

from app.api.core import notification_events
from app.api.core.clock import utcnow
from app.api.core.service_payment_provider import (
    SandboxProvider,
    WebhookRejected,
    get_service_payment_provider,
)
from app.api.core.visit_chain import money
from app.api.crud import crud_public_booking
from app.db.models.public_booking import PublicBookingSettings
from app.db.models.service_payment import (
    ServicePaymentAttempt,
    ServicePaymentEvent,
    ServicePaymentRefund,
)
from app.db.models.visit import Visit
from app.db.models.visit_event import VisitEvent
from app.schemas.service_payment import (
    PublicPaymentOut,
    PublicPaymentStatusOut,
    ServicePaymentOut,
    ServicePaymentRefundOut,
)

PAID_STATUSES = ("paid", "partially_refunded", "refunded")
REFUNDABLE_STATUSES = ("paid", "reconcile", "partially_refunded")
PENDING = "pending"
CENT = Decimal("0.01")


class PaymentUnavailable(Exception):
    """Checkout could not be created; the booking must not be confirmed."""


class PaymentNotFound(Exception):
    pass


class RefundError(Exception):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class WebhookResult:
    attempt: ServicePaymentAttempt
    duplicate: bool
    changed: bool


def _token_hash(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _now() -> datetime:
    return utcnow()


def deposit_amount(total: Decimal, deposit_type: str, deposit_value) -> Decimal:
    """Fixed is capped by the visit total; percentage is rounded to cents by the server."""
    if total <= 0 or deposit_type == "none":
        return Decimal("0.00")
    if deposit_type == "fixed":
        amount = money(deposit_value or 0)
    elif deposit_type == "percentage":
        amount = (total * Decimal(str(deposit_value or 0)) / Decimal("100")).quantize(
            CENT, rounding=ROUND_HALF_UP
        )
    else:
        return Decimal("0.00")
    if amount <= 0:
        return Decimal("0.00")
    return min(amount, total)


def effective_policy(
    db: Session, business_id: int
) -> tuple[PublicBookingSettings | None, SandboxProvider | None]:
    provider = get_service_payment_provider()
    settings = crud_public_booking.get_settings(db, business_id)
    return settings, provider


def _payment_totals(visit: Visit, settings: PublicBookingSettings) -> tuple[Decimal, Decimal]:
    raw_total = visit.price
    total = money(raw_total) if raw_total is not None else Decimal("0.00")
    amount = deposit_amount(total, settings.deposit_type, settings.deposit_value)
    return amount, money(total - amount)


def create_attempt(
    db: Session,
    business,
    visit: Visit,
    settings: PublicBookingSettings,
    provider: SandboxProvider,
    request_key: str,
) -> ServicePaymentAttempt | None:
    amount, remaining = _payment_totals(visit, settings)
    if amount <= 0:
        return None
    hold_minutes = max(5, min(int(settings.deposit_hold_minutes or 30), 1440))
    public_ref = secrets.token_urlsafe(18)
    public_token = secrets.token_urlsafe(32)
    attempt = ServicePaymentAttempt(
        business_id=business.id,
        visit_id=visit.id,
        provider=provider.name,
        merchant=provider.merchant,
        request_key=request_key[:80],
        public_ref=public_ref,
        public_token_hash=_token_hash(public_token),
        status=PENDING,
        currency=business.currency,
        amount=amount,
        remaining_amount=remaining,
        refunded_amount=Decimal("0.00"),
        expires_at=_now() + timedelta(minutes=hold_minutes),
    )
    db.add(attempt)
    # the retry key is on disk before the provider is asked to create anything
    db.flush()
    try:
        checkout = provider.create_checkout(
            public_ref=public_ref,
            public_token=public_token,
            amount=str(amount),
            currency=business.currency,
        )
    except Exception as exc:  # provider-side failure must never confirm the booking
        raise PaymentUnavailable() from exc
    attempt.provider_ref = checkout.provider_ref
    attempt.checkout_url = checkout.checkout_url
    visit.status = "awaiting_payment"
    db.flush()
    if attempt not in visit.payment_attempts:
        visit.payment_attempts.append(attempt)
    visit._payment_attempt = attempt  # transient hint for the response, not persisted
    return attempt


def latest_attempt(db: Session, visit: Visit) -> ServicePaymentAttempt | None:
    hint = getattr(visit, "_payment_attempt", None)
    if hint is not None:
        return hint
    return (
        db.query(ServicePaymentAttempt)
        .filter(ServicePaymentAttempt.visit_id == visit.id)
        .order_by(ServicePaymentAttempt.id.desc())
        .first()
    )


def payment_out(attempt: ServicePaymentAttempt | None) -> PublicPaymentOut | None:
    if attempt is None:
        return None
    active = attempt.status == PENDING and attempt.expires_at > _now()
    return PublicPaymentOut(
        public_ref=attempt.public_ref,
        status=attempt.status,
        amount=float(attempt.amount),
        remaining_amount=float(attempt.remaining_amount),
        refunded_amount=float(attempt.refunded_amount or 0),
        currency=attempt.currency,
        checkout_url=attempt.checkout_url if active else None,
        hold_expires_at=attempt.expires_at,
        paid_at=attempt.paid_at,
    )


def payment_status_out(attempt: ServicePaymentAttempt) -> PublicPaymentStatusOut:
    visit = attempt.visit
    business = visit.business if visit else None
    base = payment_out(attempt)
    return PublicPaymentStatusOut(
        **base.model_dump(),
        visit_status=visit.status if visit else "cancelled",
        visit_start=visit.date_start,
        visit_end=visit.date_end or visit.date_start,
        service_name=visit.service_name or "",
        timezone=business.timezone if business else "UTC",
    )


def _cancel_hold(db: Session, visit: Visit, reason: str) -> None:
    if visit.status != "awaiting_payment":
        return
    visit.status = "cancelled"
    key = f"visit:{visit.id}:hold-{reason}"
    exists = db.query(VisitEvent.id).filter(VisitEvent.event_key == key).first()
    if not exists:
        db.add(
            VisitEvent(
                business_id=visit.business_id,
                visit_id=visit.id,
                event_type="cancelled",
                actor="system",
                event_key=key,
            )
        )


def expire_stale_holds(
    db: Session,
    business_id: Optional[int] = None,
    *,
    now: Optional[datetime] = None,
    commit: bool = True,
) -> int:
    """Release unpaid holds lazily, the first time anyone looks at their slot or payment."""
    moment = now or _now()
    query = db.query(ServicePaymentAttempt).filter(
        ServicePaymentAttempt.status == PENDING,
        ServicePaymentAttempt.expires_at <= moment,
    )
    if business_id is not None:
        query = query.filter(ServicePaymentAttempt.business_id == business_id)
    attempts = query.with_for_update().all()
    for attempt in attempts:
        attempt.status = "expired"
        visit = (
            db.query(Visit)
            .filter(Visit.id == attempt.visit_id)
            .with_for_update()
            .first()
        )
        if visit is not None:
            _cancel_hold(db, visit, "expired")
    if attempts and commit:
        db.commit()
    return len(attempts)


def _has_conflict(db: Session, visit: Visit) -> bool:
    """Whether another visit now occupies the held time; only a race could produce this."""
    own = list(visit.items)
    if not own:
        return False
    staff_ids = {item.specialist_id for item in own if item.specialist_id is not None}
    if not staff_ids:
        return False
    margin = timedelta(
        minutes=max(
            [(item.buffer_before_minutes or 0) + (item.buffer_after_minutes or 0) for item in own]
            or [0]
        )
        + 1
    )
    others = (
        db.query(Visit)
        .filter(
            Visit.id != visit.id,
            Visit.status != "cancelled",
            Visit.deleted_at.is_(None),
            Visit.archived_at.is_(None),
            Visit.date_start < (visit.date_end or visit.date_start) + margin,
            Visit.date_end > visit.date_start - margin,
        )
        .all()
    )
    own_intervals = [
        (
            item.specialist_id,
            item.date_start - timedelta(minutes=item.buffer_before_minutes or 0),
            item.date_end + timedelta(minutes=item.buffer_after_minutes or 0),
        )
        for item in own
    ]
    for other in others:
        for item in other.items:
            if item.specialist_id not in staff_ids:
                continue
            start = item.date_start - timedelta(minutes=item.buffer_before_minutes or 0)
            end = item.date_end + timedelta(minutes=item.buffer_after_minutes or 0)
            for staff_id, own_start, own_end in own_intervals:
                if staff_id == item.specialist_id and start < own_end and end > own_start:
                    return True
    return False


def _apply_event(
    db: Session, attempt: ServicePaymentAttempt, event_type: str, now: datetime
) -> bool:
    """Apply one verified event; returns whether the attempt changed."""
    visit = attempt.visit
    if event_type == "payment.paid":
        if attempt.status in PAID_STATUSES:
            return False
        if attempt.status == PENDING:
            if attempt.expires_at <= now or visit is None or visit.status != "awaiting_payment":
                attempt.status = "reconcile"
                if visit is not None and visit.status == "awaiting_payment":
                    _cancel_hold(db, visit, "conflict")
                return True
            if _has_conflict(db, visit):
                attempt.status = "reconcile"
                _cancel_hold(db, visit, "conflict")
                return True
            attempt.status = "paid"
            attempt.paid_at = now
            visit.status = "planned"
            notification_events.queue_booking_created(db, visit.business, visit, visit.client)
            return True
        # a failed/expired/cancelled attempt that got money anyway is not silently confirmed
        attempt.status = "reconcile"
        return True
    if event_type == "payment.failed":
        if attempt.status == PENDING:
            attempt.status = "failed"
            if visit is not None:
                _cancel_hold(db, visit, "failed")
            return True
        return False
    if event_type == "payment.pending":
        return False
    raise WebhookRejected("event_unsupported")


def handle_webhook(
    db: Session,
    raw: bytes,
    signature: Optional[str],
    merchant_header: Optional[str],
) -> WebhookResult:
    provider = get_service_payment_provider()
    if provider is None:
        raise WebhookRejected("provider_unavailable")
    payload = provider.parse_webhook(raw, signature, merchant_header)
    event_type = payload["type"]
    if event_type not in ("payment.paid", "payment.failed", "payment.pending"):
        raise WebhookRejected("event_unsupported")

    attempt = (
        db.query(ServicePaymentAttempt)
        .filter(
            ServicePaymentAttempt.provider == provider.name,
            ServicePaymentAttempt.provider_ref == payload["payment_ref"],
        )
        .with_for_update()
        .first()
    )
    if attempt is None:
        raise PaymentNotFound()
    if attempt.merchant != provider.merchant:
        raise WebhookRejected("merchant_mismatch")

    existing = (
        db.query(ServicePaymentEvent)
        .filter(
            ServicePaymentEvent.provider == provider.name,
            ServicePaymentEvent.provider_event_id == payload["id"],
        )
        .first()
    )
    if existing is not None:
        if existing.attempt_id != attempt.id:
            raise WebhookRejected("event_conflict")
        return WebhookResult(attempt=attempt, duplicate=True, changed=False)

    if payload["currency"] != attempt.currency:
        raise WebhookRejected("currency_mismatch")
    try:
        paid_amount = money(payload["amount"])
    except Exception:
        raise WebhookRejected("amount_invalid")
    if paid_amount != money(attempt.amount):
        raise WebhookRejected("amount_mismatch")

    now = _now()
    changed = _apply_event(db, attempt, event_type, now)
    attempt.updated_at = now
    db.add(
        ServicePaymentEvent(
            attempt_id=attempt.id,
            business_id=attempt.business_id,
            provider=provider.name,
            provider_event_id=payload["id"][:128],
            event_type=event_type,
            status=attempt.status,
            source="webhook",
            payload_hash=hashlib.sha256(raw).hexdigest(),
        )
    )
    db.commit()
    db.refresh(attempt)
    return WebhookResult(attempt=attempt, duplicate=False, changed=changed)


def resolve_public_attempt(
    db: Session, public_ref: str, public_token: Optional[str]
) -> ServicePaymentAttempt:
    if not public_token:
        raise PaymentNotFound()
    attempt = (
        db.query(ServicePaymentAttempt)
        .filter(ServicePaymentAttempt.public_ref == public_ref)
        .first()
    )
    if attempt is None or not secrets.compare_digest(
        attempt.public_token_hash, _token_hash(public_token)
    ):
        raise PaymentNotFound()
    if attempt.status == PENDING and attempt.expires_at <= _now():
        expire_stale_holds(db, business_id=attempt.business_id)
        db.refresh(attempt)
    return attempt


def mark_attempt_cancelled(db: Session, visit: Visit) -> None:
    """A guest released an unpaid hold; a late payment will be flagged for reconcile."""
    attempt = (
        db.query(ServicePaymentAttempt)
        .filter(
            ServicePaymentAttempt.visit_id == visit.id,
            ServicePaymentAttempt.status == PENDING,
        )
        .with_for_update()
        .first()
    )
    if attempt is not None:
        attempt.status = "cancelled"
        attempt.updated_at = _now()


def apply_sandbox_outcome(
    db: Session, public_ref: str, public_token: Optional[str], outcome: str
) -> ServicePaymentAttempt:
    provider = get_service_payment_provider()
    if provider is None:
        raise PaymentNotFound()
    attempt = resolve_public_attempt(db, public_ref, public_token)
    if attempt.provider != provider.name or not attempt.provider_ref:
        raise PaymentNotFound()
    if attempt.status != PENDING:
        return attempt
    raw, signature = provider.build_event(
        event_id=f"evt_{secrets.token_urlsafe(18)}",
        event_type=f"payment.{outcome}",
        payment_ref=attempt.provider_ref,
        amount=str(money(attempt.amount)),
        currency=attempt.currency,
    )
    handle_webhook(db, raw, signature, provider.merchant)
    db.refresh(attempt)
    return attempt


def refund_out(row: ServicePaymentRefund) -> ServicePaymentRefundOut:
    return ServicePaymentRefundOut(
        id=row.id,
        amount=float(row.amount),
        currency=row.currency,
        reason=row.reason,
        status=row.status,
        created_at=row.created_at,
    )


def refund(
    db: Session,
    business_id: int,
    attempt_id: int,
    refund_key: str,
    amount: Optional[float],
    reason: Optional[str],
) -> ServicePaymentRefund:
    attempt = (
        db.query(ServicePaymentAttempt)
        .filter(
            ServicePaymentAttempt.id == attempt_id,
            ServicePaymentAttempt.business_id == business_id,
        )
        .with_for_update()
        .first()
    )
    if attempt is None:
        raise RefundError("payment_not_found")

    existing = (
        db.query(ServicePaymentRefund)
        .filter(
            ServicePaymentRefund.attempt_id == attempt.id,
            ServicePaymentRefund.refund_key == refund_key,
        )
        .first()
    )
    if existing is not None:
        if amount is not None and money(amount) != money(existing.amount):
            raise RefundError("refund_key_conflict")
        return existing

    if attempt.status not in REFUNDABLE_STATUSES:
        raise RefundError("refund_unavailable")
    provider = get_service_payment_provider()
    if provider is None or provider.name != attempt.provider:
        raise RefundError("refund_provider_unavailable")

    available = money(money(attempt.amount) - money(attempt.refunded_amount or 0))
    requested = available if amount is None else money(amount)
    if requested <= 0 or requested > available:
        raise RefundError("refund_amount_invalid")

    provider_ref = provider.refund(attempt, requested)
    row = ServicePaymentRefund(
        attempt_id=attempt.id,
        business_id=business_id,
        provider=provider.name,
        refund_key=refund_key,
        provider_refund_ref=provider_ref,
        amount=requested,
        currency=attempt.currency,
        reason=(reason or "")[:255] or None,
        status="succeeded",
    )
    db.add(row)
    db.flush()
    attempt.refunded_amount = money(money(attempt.refunded_amount or 0) + requested)
    attempt.status = "refunded" if attempt.refunded_amount >= money(attempt.amount) else "partially_refunded"
    attempt.updated_at = _now()
    db.add(
        ServicePaymentEvent(
            attempt_id=attempt.id,
            business_id=business_id,
            provider=provider.name,
            provider_event_id=f"internal:refund:{attempt.id}:{refund_key}"[:128],
            event_type="refund.succeeded",
            status=attempt.status,
            source="internal",
            payload_hash=hashlib.sha256(f"{refund_key}:{requested}".encode("utf-8")).hexdigest(),
        )
    )
    db.commit()
    db.refresh(row)
    return row


def staff_payment_out(attempt: ServicePaymentAttempt) -> ServicePaymentOut:
    visit = attempt.visit
    refundable = (
        money(attempt.amount) - money(attempt.refunded_amount or 0)
        if attempt.status in REFUNDABLE_STATUSES
        else Decimal("0.00")
    )
    return ServicePaymentOut(
        id=attempt.id,
        visit_id=attempt.visit_id,
        status=attempt.status,
        provider=attempt.provider,
        client_name=visit.client.full_name if visit and visit.client else "",
        service_name=(visit.service_name or "") if visit else "",
        visit_start=visit.date_start if visit else attempt.created_at,
        visit_status=visit.status if visit else "cancelled",
        currency=attempt.currency,
        amount=float(attempt.amount),
        remaining_amount=float(attempt.remaining_amount),
        refunded_amount=float(attempt.refunded_amount or 0),
        refundable_amount=float(max(refundable, Decimal("0.00"))),
        hold_expires_at=attempt.expires_at,
        paid_at=attempt.paid_at,
        created_at=attempt.created_at,
        refunds=[refund_out(row) for row in attempt.refunds],
    )


def list_attempts(
    db: Session,
    business_id: int,
    *,
    status: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
) -> list[ServicePaymentAttempt]:
    expire_stale_holds(db, business_id=business_id)
    query = db.query(ServicePaymentAttempt).filter(
        ServicePaymentAttempt.business_id == business_id
    )
    if status:
        query = query.filter(ServicePaymentAttempt.status == status)
    return (
        query.order_by(ServicePaymentAttempt.id.desc())
        .offset(max(0, offset))
        .limit(max(1, min(limit, 200)))
        .all()
    )
