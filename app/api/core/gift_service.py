"""Issuing, redeeming, reversing and expiring salon gift certificates.

Every balance is the sum of an immutable ledger. A redemption locks the certificate
row, so two tills cannot spend the last euro or the last visit twice.
"""
import hashlib
import json
import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Optional

from sqlalchemy import case, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.core.booking_slug import normalize_slug
from app.api.core.clock import utcnow
from app.api.core.service_payment_provider import SandboxProvider, WebhookRejected
from app.api.core.visit_chain import money
from app.db.models.business import Business
from app.db.models.gift import (
    GiftCertificate,
    GiftLedgerEntry,
    GiftOffer,
    GiftOrder,
    GiftPaymentEvent,
)
from app.db.models.public_booking import PublicBookingSettings
from app.db.models.service import Service
from app.db.models.visit import Visit

CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
ACTIVE_STATUS = "active"
EXPIRED_STATUS = "expired"
EXHAUSTED_STATUS = "exhausted"
PENDING_ORDER = "pending"
PAID_ORDER = "paid"
PAID_ORDER_STATUSES = (PAID_ORDER,)
REDEEMABLE_STATUSES = (ACTIVE_STATUS,)
HOLD_MINUTES = 30


class GiftUnavailable(Exception):
    pass


class GiftNotFound(Exception):
    pass


class GiftError(Exception):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class GiftWebhookResult:
    order: GiftOrder
    duplicate: bool
    changed: bool


def _now() -> datetime:
    return utcnow()


def canonical_code(raw: str) -> str:
    compact = re.sub(r"[^A-Z0-9]", "", (raw or "").upper())
    return compact


def code_digest(raw: str) -> str:
    return hashlib.sha256(canonical_code(raw).encode("utf-8")).hexdigest()


def token_digest(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _new_code() -> str:
    compact = "".join(secrets.choice(CODE_ALPHABET) for _ in range(12))
    return f"GIFT-{compact[:4]}-{compact[4:8]}-{compact[8:]}"


def _request_hash(payload) -> str:
    body = {
        "offer_id": payload.offer_id,
        "buyer_name": str(payload.buyer_name),
        "buyer_email": str(payload.buyer_email).lower(),
        "recipient_name": str(payload.recipient_name or ""),
        "recipient_email": str(payload.recipient_email or "").lower(),
        "message": payload.message or "",
    }
    raw = json.dumps(body, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _service_ids(value) -> list[int]:
    if not value:
        return []
    return [int(item) for item in value]


def offer_out(offer: GiftOffer) -> dict:
    return {
        "id": offer.id,
        "business_id": offer.business_id,
        "name": offer.name,
        "description": offer.description,
        "kind": offer.kind,
        "price": float(offer.price),
        "currency": offer.currency,
        "face_value": float(offer.face_value) if offer.face_value is not None else None,
        "visits_count": offer.visits_count,
        "validity_days": offer.validity_days,
        "service_ids": _service_ids(offer.service_ids),
        "is_active": bool(offer.is_active),
        "created_at": offer.created_at,
        "updated_at": offer.updated_at,
    }


def _validate_services(db: Session, business_id: int, service_ids: list[int]) -> None:
    if not service_ids:
        return
    own = (
        db.query(Service.id)
        .filter(Service.id.in_(service_ids), Service.business_id == business_id)
        .count()
    )
    if own != len(set(service_ids)):
        raise GiftError("gift_service_invalid")


def list_offers(db: Session, business_id: int, *, include_inactive: bool = True) -> list[GiftOffer]:
    query = db.query(GiftOffer).filter(GiftOffer.business_id == business_id)
    if not include_inactive:
        query = query.filter(GiftOffer.is_active.is_(True))
    return query.order_by(GiftOffer.id.desc()).all()


def get_offer(db: Session, business_id: int, offer_id: int) -> GiftOffer:
    offer = (
        db.query(GiftOffer)
        .filter(GiftOffer.id == offer_id, GiftOffer.business_id == business_id)
        .first()
    )
    if offer is None:
        raise GiftNotFound()
    return offer


def create_offer(db: Session, business: Business, payload) -> GiftOffer:
    _validate_services(db, business.id, list(payload.service_ids))
    offer = GiftOffer(
        business_id=business.id,
        name=payload.name,
        description=payload.description,
        kind=payload.kind,
        price=money(payload.price),
        currency=business.currency,
        face_value=money(payload.face_value) if payload.kind == "money" else None,
        visits_count=payload.visits_count if payload.kind == "package" else None,
        validity_days=payload.validity_days,
        service_ids=list(payload.service_ids) or None,
        is_active=payload.is_active,
    )
    db.add(offer)
    db.commit()
    db.refresh(offer)
    return offer


def update_offer(db: Session, business_id: int, offer_id: int, payload) -> GiftOffer:
    offer = get_offer(db, business_id, offer_id)
    _validate_services(db, business_id, list(payload.service_ids))
    offer.name = payload.name
    offer.description = payload.description
    offer.kind = payload.kind
    offer.price = money(payload.price)
    offer.face_value = money(payload.face_value) if payload.kind == "money" else None
    offer.visits_count = payload.visits_count if payload.kind == "package" else None
    offer.validity_days = payload.validity_days
    offer.service_ids = list(payload.service_ids) or None
    offer.is_active = payload.is_active
    db.commit()
    db.refresh(offer)
    return offer


def published_offers(db: Session, business: Business) -> list[GiftOffer]:
    return (
        db.query(GiftOffer)
        .filter(GiftOffer.business_id == business.id, GiftOffer.is_active.is_(True))
        .order_by(GiftOffer.id.asc())
        .all()
    )


def get_shop(db: Session, slug: str, *, write: bool = False) -> tuple[Business, str]:
    """The salon's public handle, created with its online booking link."""
    settings = (
        db.query(PublicBookingSettings)
        .join(Business, PublicBookingSettings.business_id == Business.id)
        .filter(
            PublicBookingSettings.slug == normalize_slug(slug),
            Business.deleted_at.is_(None),
        )
        .first()
    )
    if settings is None or settings.business is None:
        raise GiftNotFound()
    from app.api.core.subscription_access import enforce_business_access
    enforce_business_access(db, settings.business_id, write=write)
    return settings.business, settings.slug


def shop_out(db: Session, business: Business, slug: str, *, payments_available: bool) -> dict:
    return {
        "slug": slug,
        "business_name": business.name,
        "currency": business.currency,
        "payments_available": payments_available,
        "offers": [offer_out(offer) for offer in published_offers(db, business)],
    }


def published_offer(db: Session, business_id: int, offer_id: int) -> GiftOffer:
    offer = (
        db.query(GiftOffer)
        .filter(
            GiftOffer.id == offer_id,
            GiftOffer.business_id == business_id,
            GiftOffer.is_active.is_(True),
        )
        .first()
    )
    if offer is None:
        raise GiftError("gift_offer_unavailable")
    return offer


def expire_stale_orders(db: Session, order: GiftOrder, now: Optional[datetime] = None) -> bool:
    if order.status == PENDING_ORDER and order.expires_at <= (now or _now()):
        order.status = "expired"
        order.updated_at = _now()
        return True
    return False


def create_order(
    db: Session,
    business: Business,
    offer: GiftOffer,
    provider: SandboxProvider,
    payload,
) -> GiftOrder:
    digest = _request_hash(payload)
    existing = (
        db.query(GiftOrder)
        .filter(
            GiftOrder.business_id == business.id,
            GiftOrder.request_key == payload.client_request_id,
        )
        .first()
    )
    if existing is not None:
        if existing.request_hash != digest:
            raise GiftError("gift_request_conflict")
        if expire_stale_orders(db, existing):
            db.commit()
        return existing

    public_ref = secrets.token_urlsafe(18)
    public_token = secrets.token_urlsafe(32)
    order = GiftOrder(
        business_id=business.id,
        offer_id=offer.id,
        provider=provider.name,
        merchant=provider.merchant,
        request_key=payload.client_request_id,
        request_hash=digest,
        public_ref=public_ref,
        public_token_hash=token_digest(public_token),
        status=PENDING_ORDER,
        offer_snapshot={
            "name": offer.name, "kind": offer.kind,
            "face_value": str(offer.face_value) if offer.face_value is not None else None,
            "visits_count": offer.visits_count, "service_ids": list(offer.service_ids or []),
            "validity_days": offer.validity_days,
        },
        amount=money(offer.price),
        currency=offer.currency,
        buyer_name=payload.buyer_name,
        buyer_email=str(payload.buyer_email),
        recipient_name=payload.recipient_name,
        recipient_email=str(payload.recipient_email) if payload.recipient_email else None,
        message=payload.message,
        expires_at=_now() + timedelta(minutes=HOLD_MINUTES),
    )
    db.add(order)
    db.flush()
    try:
        checkout = provider.create_checkout(
            public_ref=public_ref,
            public_token=public_token,
            amount=str(money(offer.price)),
            currency=offer.currency,
            checkout_path="/gift/checkout",
        )
    except Exception as exc:
        db.rollback()
        raise GiftUnavailable() from exc
    order.provider_ref = checkout.provider_ref
    order.checkout_url = checkout.checkout_url
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        existing = (
            db.query(GiftOrder)
            .filter(
                GiftOrder.business_id == business.id,
                GiftOrder.request_key == payload.client_request_id,
            )
            .first()
        )
        if existing is None:
            raise GiftError("gift_request_conflict")
        if existing.request_hash != digest:
            raise GiftError("gift_request_conflict")
        return existing
    db.refresh(order)
    return order


def _totals(db: Session, certificate_id: int) -> tuple[Decimal, int]:
    amount, units = (
        db.query(
            func.coalesce(func.sum(GiftLedgerEntry.amount_delta), 0),
            func.coalesce(func.sum(GiftLedgerEntry.units_delta), 0),
        )
        .filter(GiftLedgerEntry.certificate_id == certificate_id)
        .one()
    )
    return money(amount), int(units)


def _refresh_status(db: Session, certificate: GiftCertificate, now: Optional[datetime] = None) -> None:
    # Reads can write an expiry entry too. Use the same lock as redemption and
    # reload stale ORM state before deciding how much remains to expire.
    certificate = db.query(GiftCertificate).filter(
        GiftCertificate.id == certificate.id
    ).populate_existing().with_for_update().one()
    if certificate.status == EXPIRED_STATUS:
        return
    moment = now or _now()
    if certificate.expires_at <= moment:
        _expire_certificate(db, certificate, moment)
        return
    amount, units = _totals(db, certificate.id)
    exhausted = amount <= 0 if certificate.kind == "money" else units <= 0
    certificate.status = EXHAUSTED_STATUS if exhausted else ACTIVE_STATUS


def _expire_certificate(db: Session, certificate: GiftCertificate, now: datetime) -> None:
    amount, units = _totals(db, certificate.id)
    if amount > 0 or units > 0:
        db.add(
            GiftLedgerEntry(
                certificate_id=certificate.id,
                business_id=certificate.business_id,
                entry_type="expired",
                amount_delta=-amount if amount > 0 else Decimal("0.00"),
                units_delta=-units if units > 0 else 0,
                request_key=f"expire:{certificate.id}",
                reason="validity ended",
            )
        )
        db.flush()
    certificate.status = EXPIRED_STATUS
    certificate.updated_at = now


def expire_certificate(db: Session, certificate: GiftCertificate) -> None:
    locked = (
        db.query(GiftCertificate)
        .filter(GiftCertificate.id == certificate.id)
        .populate_existing()
        .with_for_update()
        .one()
    )
    _refresh_status(db, locked)
    db.commit()
    db.refresh(certificate)


def _find_certificate(db: Session, business_id: int, code: str) -> GiftCertificate:
    certificate = (
        db.query(GiftCertificate)
        .filter(
            GiftCertificate.business_id == business_id,
            GiftCertificate.code_hash == code_digest(code),
        )
        .populate_existing()
        .with_for_update()
        .first()
    )
    if certificate is None:
        raise GiftNotFound()
    _refresh_status(db, certificate)
    return certificate


def certificate_out(
    db: Session, certificate: GiftCertificate, *, reveal_code: bool = False
) -> dict:
    if certificate.status != EXPIRED_STATUS:
        _refresh_status(db, certificate)
    amount, units = _totals(db, certificate.id)
    return {
        "id": certificate.id,
        "status": certificate.status,
        "kind": certificate.kind,
        "code": certificate.code if reveal_code else None,
        "code_last4": certificate.code_last4,
        "issuer_name": certificate.issuer_name,
        "offer_name": certificate.offer_name,
        "currency": certificate.currency,
        "face_value": float(certificate.face_value),
        "visits_total": int(certificate.visits_total),
        "money_balance": float(amount),
        "visits_remaining": int(units),
        "service_ids": _service_ids(certificate.service_ids),
        "recipient_name": certificate.recipient_name,
        "recipient_email": certificate.recipient_email,
        "buyer_name": certificate.buyer_name,
        "message": certificate.message,
        "issued_at": certificate.issued_at,
        "expires_at": certificate.expires_at,
    }


def certificate_detail_out(db: Session, certificate: GiftCertificate) -> dict:
    data = certificate_out(db, certificate)
    data["order_id"] = certificate.order_id
    data["ledger"] = [
        {
            "id": row.id,
            "entry_type": row.entry_type,
            "amount_delta": float(row.amount_delta),
            "units_delta": int(row.units_delta),
            "request_key": row.request_key,
            "visit_id": row.visit_id,
            "redeems_entry_id": row.redeems_entry_id,
            "reason": row.reason,
            "created_at": row.created_at,
        }
        for row in certificate.entries
    ]
    return data


def order_out(
    db: Session,
    order: GiftOrder,
    *,
    reveal_code: bool = False,
) -> dict:
    if expire_stale_orders(db, order):
        db.commit()
        db.refresh(order)
    certificate = order.certificate
    active = order.status == PENDING_ORDER
    return {
        "public_ref": order.public_ref,
        "status": order.status,
        "amount": float(order.amount),
        "currency": order.currency,
        "offer_name": order.offer_snapshot["name"],
        "kind": order.offer_snapshot["kind"],
        "buyer_name": order.buyer_name,
        "recipient_name": order.recipient_name or order.buyer_name,
        "checkout_url": order.checkout_url if active else None,
        "hold_expires_at": order.expires_at,
        "paid_at": order.paid_at,
        "certificate": certificate_out(db, certificate, reveal_code=reveal_code)
        if certificate is not None
        else None,
    }


def resolve_order(db: Session, public_ref: str, public_token: Optional[str], *, write: bool = False) -> GiftOrder:
    if not public_token:
        raise GiftNotFound()
    order = (
        db.query(GiftOrder)
        .filter(GiftOrder.public_ref == public_ref)
        .first()
    )
    if order is None or not secrets.compare_digest(
        order.public_token_hash, token_digest(public_token)
    ):
        raise GiftNotFound()
    from app.api.core.subscription_access import enforce_business_access
    enforce_business_access(db, order.business_id, write=write)
    if expire_stale_orders(db, order):
        db.commit()
        db.refresh(order)
    return order


def _issue_certificate(db: Session, order: GiftOrder, now: datetime) -> GiftCertificate:
    existing = (
        db.query(GiftCertificate)
        .filter(GiftCertificate.order_id == order.id)
        .first()
    )
    if existing is not None:
        return existing
    terms = order.offer_snapshot
    business = order.business if hasattr(order, "business") else db.get(Business, order.business_id)
    code = _new_code()
    while db.query(GiftCertificate.id).filter(GiftCertificate.code_hash == code_digest(code)).first():
        code = _new_code()
    certificate = GiftCertificate(
        business_id=order.business_id,
        offer_id=order.offer_id,
        order_id=order.id,
        code=code,
        code_hash=code_digest(code),
        code_last4=canonical_code(code)[-4:],
        kind=terms["kind"],
        status=ACTIVE_STATUS,
        issuer_name=business.name if business else "",
        offer_name=terms["name"],
        currency=order.currency,
        face_value=money(terms["face_value"]) if terms["kind"] == "money" else Decimal("0.00"),
        visits_total=int(terms["visits_count"] or 0) if terms["kind"] == "package" else 0,
        service_ids=_service_ids(terms["service_ids"]) or None,
        recipient_name=order.recipient_name or order.buyer_name,
        recipient_email=order.recipient_email,
        buyer_name=order.buyer_name,
        buyer_email=order.buyer_email,
        message=order.message,
        issued_at=now,
        expires_at=now + timedelta(days=terms["validity_days"]),
    )
    db.add(certificate)
    db.flush()
    db.add(
        GiftLedgerEntry(
            certificate_id=certificate.id,
            business_id=order.business_id,
            entry_type="issued",
            amount_delta=certificate.face_value,
            units_delta=certificate.visits_total,
            request_key=f"issue:{order.id}",
            reason="purchase paid",
        )
    )
    db.flush()
    return certificate


def _apply_order_event(db: Session, order: GiftOrder, event_type: str, now: datetime) -> bool:
    if event_type == "payment.paid":
        if order.status in PAID_ORDER_STATUSES:
            return False
        if order.status != PENDING_ORDER:
            order.status = "reconcile"
            return True
        if order.expires_at <= now:
            order.status = "reconcile"
            return True
        order.status = PAID_ORDER
        order.paid_at = now
        _issue_certificate(db, order, now)
        return True
    if event_type == "payment.failed":
        if order.status == PENDING_ORDER:
            order.status = "failed"
            return True
        return False
    if event_type == "payment.pending":
        return False
    raise WebhookRejected("event_unsupported")


def handle_webhook(
    db: Session,
    provider: SandboxProvider,
    raw: bytes,
    signature: Optional[str],
    merchant_header: Optional[str],
) -> GiftWebhookResult:
    payload = provider.parse_webhook(raw, signature, merchant_header)
    event_type = payload["type"]
    if event_type not in ("payment.paid", "payment.failed", "payment.pending"):
        raise WebhookRejected("event_unsupported")

    order = (
        db.query(GiftOrder)
        .filter(
            GiftOrder.provider == provider.name,
            GiftOrder.provider_ref == payload["payment_ref"],
        )
        .populate_existing()
        .with_for_update()
        .first()
    )
    if order is None:
        raise GiftNotFound()
    if order.merchant != provider.merchant:
        raise WebhookRejected("merchant_mismatch")

    existing = (
        db.query(GiftPaymentEvent)
        .filter(
            GiftPaymentEvent.provider == provider.name,
            GiftPaymentEvent.provider_event_id == payload["id"],
        )
        .first()
    )
    if existing is not None:
        if existing.order_id != order.id:
            raise WebhookRejected("event_conflict")
        return GiftWebhookResult(order=order, duplicate=True, changed=False)

    if payload["currency"] != order.currency:
        raise WebhookRejected("currency_mismatch")
    try:
        paid_amount = money(payload["amount"])
    except Exception:
        raise WebhookRejected("amount_invalid")
    if paid_amount != money(order.amount):
        raise WebhookRejected("amount_mismatch")

    now = _now()
    changed = _apply_order_event(db, order, event_type, now)
    order.updated_at = now
    db.add(
        GiftPaymentEvent(
            order_id=order.id,
            business_id=order.business_id,
            provider=provider.name,
            provider_event_id=payload["id"][:128],
            event_type=event_type,
            status=order.status,
            source="webhook",
            payload_hash=hashlib.sha256(raw).hexdigest(),
        )
    )
    db.commit()
    db.refresh(order)
    return GiftWebhookResult(order=order, duplicate=False, changed=changed)


def apply_sandbox_outcome(
    db: Session,
    provider: SandboxProvider,
    public_ref: str,
    public_token: Optional[str],
    outcome: str,
) -> GiftOrder:
    order = resolve_order(db, public_ref, public_token)
    if order.provider != provider.name or not order.provider_ref:
        raise GiftNotFound()
    if order.status != PENDING_ORDER:
        return order
    raw, signature = provider.build_event(
        event_id=f"evt_{secrets.token_urlsafe(18)}",
        event_type=f"payment.{outcome}",
        payment_ref=order.provider_ref,
        amount=str(money(order.amount)),
        currency=order.currency,
    )
    handle_webhook(db, provider, raw, signature, provider.merchant)
    db.refresh(order)
    return order


def _visit_service_ids(visit: Visit) -> set[int]:
    ids = {item.service_id for item in visit.items if item.service_id}
    if visit.service_id:
        ids.add(visit.service_id)
    return ids


def _check_scope(certificate: GiftCertificate, visit: Visit) -> None:
    scope = set(_service_ids(certificate.service_ids))
    if not scope:
        return
    used = _visit_service_ids(visit)
    if not used or not used.issubset(scope):
        raise GiftError("gift_scope_mismatch")


def _lock_certificate(db: Session, business_id: int, code: str) -> GiftCertificate:
    try:
        return _find_certificate(db, business_id, code)
    except GiftNotFound:
        raise GiftError("gift_code_invalid")


def _existing_entry(
    db: Session, certificate_id: int, request_key: str
) -> GiftLedgerEntry | None:
    return (
        db.query(GiftLedgerEntry)
        .filter(
            GiftLedgerEntry.certificate_id == certificate_id,
            GiftLedgerEntry.request_key == request_key,
        )
        .first()
    )


def redeem(
    db: Session,
    business_id: int,
    code: str,
    visit_id: int,
    amount: Optional[float],
    reason: Optional[str],
    request_key: str,
    actor_user_id: Optional[int] = None,
) -> tuple[GiftCertificate, GiftLedgerEntry, Decimal, int]:
    visit = (
        db.query(Visit)
        .filter(Visit.id == visit_id, Visit.business_id == business_id, Visit.deleted_at.is_(None))
        .populate_existing()
        .with_for_update()
        .first()
    )
    if visit is None or visit.status == "cancelled":
        raise GiftError("gift_visit_invalid")

    if actor_user_id is not None:
        from app.api.core.permissions import check_visit_access
        from app.db.models.user import User
        actor = db.get(User, actor_user_id)
        if actor is None or not check_visit_access(db, actor, visit):
            raise GiftError("gift_visit_invalid")

    certificate = _lock_certificate(db, business_id, code)
    _check_scope(certificate, visit)

    existing = _existing_entry(db, certificate.id, request_key)
    if existing is not None:
        if existing.entry_type != "redeemed" or existing.visit_id != visit.id:
            raise GiftError("gift_request_conflict")
        if certificate.kind == "money":
            applied = -money(existing.amount_delta)
            if amount is not None and applied != money(amount):
                raise GiftError("gift_request_conflict")
            # An automatic amount is fixed by the first operation. Replaying it
            # must not depend on later payments, reversals or balance changes.
        if certificate.kind == "package" and int(existing.units_delta) != -1:
            raise GiftError("gift_request_conflict")
        db.commit()
        return (
            certificate,
            existing,
            -money(existing.amount_delta),
            -int(existing.units_delta),
        )

    if certificate.status == EXPIRED_STATUS:
        raise GiftError("gift_expired")
    if certificate.status not in REDEEMABLE_STATUSES:
        raise GiftError("gift_insufficient_balance")

    # The visit lock also serializes payments made with different certificates.
    total_money, total_units = db.query(
        func.coalesce(func.sum(GiftLedgerEntry.amount_delta), 0),
        func.coalesce(func.sum(GiftLedgerEntry.units_delta), 0),
    ).filter(GiftLedgerEntry.visit_id == visit.id, GiftLedgerEntry.business_id == business_id,
             GiftLedgerEntry.entry_type.in_(["redeemed", "reversed"])).one()
    paid_money, paid_units = max(Decimal("0.00"), -money(total_money)), max(0, -int(total_units))
    available_money, available_units = _totals(db, certificate.id)
    spend_money = Decimal("0.00")
    spend_units = 0
    if certificate.kind == "money":
        if visit.price is None or money(visit.price) <= 0:
            raise GiftError("gift_visit_invalid")
        remaining = max(Decimal("0.00"), money(visit.price) - paid_money) if paid_units == 0 else Decimal("0.00")
        cap = min(available_money, remaining)
        spend_money = cap if amount is None else money(amount)
        if spend_money <= 0 or spend_money > cap:
            raise GiftError("gift_insufficient_balance")
    else:
        if available_units <= 0 or paid_units > 0 or paid_money > 0:
            raise GiftError("gift_insufficient_balance")
        spend_units = 1

    entry = GiftLedgerEntry(
        certificate_id=certificate.id,
        business_id=business_id,
        entry_type="redeemed",
        amount_delta=-spend_money,
        units_delta=-spend_units,
        request_key=request_key,
        visit_id=visit.id,
        actor_user_id=actor_user_id,
        reason=(reason or "")[:255] or None,
    )
    db.add(entry)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        existing = _existing_entry(db, certificate.id, request_key)
        if existing is None:
            raise GiftError("gift_request_conflict")
        if (
            existing.entry_type != "redeemed"
            or existing.visit_id != visit.id
            or money(existing.amount_delta) != -spend_money
            or int(existing.units_delta) != -spend_units
        ):
            raise GiftError("gift_request_conflict")
        db.commit()
        return certificate, existing, spend_money, spend_units
    _refresh_status(db, certificate)
    db.commit()
    db.refresh(entry)
    db.refresh(certificate)
    return certificate, entry, spend_money, spend_units


def reverse(
    db: Session,
    business_id: int,
    certificate_id: int,
    redemption_entry_id: int,
    request_key: str,
    reason: Optional[str],
    actor_user_id: Optional[int] = None,
) -> tuple[GiftCertificate, GiftLedgerEntry]:
    certificate = (
        db.query(GiftCertificate)
        .filter(
            GiftCertificate.id == certificate_id,
            GiftCertificate.business_id == business_id,
        )
        .populate_existing()
        .with_for_update()
        .first()
    )
    if certificate is None:
        raise GiftError("gift_code_invalid")
    _refresh_status(db, certificate)
    if certificate.status == EXPIRED_STATUS:
        raise GiftError("gift_expired")

    original = (
        db.query(GiftLedgerEntry)
        .filter(
            GiftLedgerEntry.id == redemption_entry_id,
            GiftLedgerEntry.certificate_id == certificate.id,
            GiftLedgerEntry.entry_type == "redeemed",
        )
        .first()
    )
    if original is None:
        raise GiftError("gift_redemption_not_found")

    existing = (
        db.query(GiftLedgerEntry)
        .filter(
            GiftLedgerEntry.certificate_id == certificate.id,
            GiftLedgerEntry.redeems_entry_id == original.id,
        )
        .first()
    )
    if existing is not None:
        db.commit()
        return certificate, existing
    duplicate = _existing_entry(db, certificate.id, request_key)
    if duplicate is not None:
        if duplicate.entry_type == "reversed" and duplicate.redeems_entry_id == original.id:
            db.commit()
            return certificate, duplicate
        raise GiftError("gift_request_conflict")

    entry = GiftLedgerEntry(
        certificate_id=certificate.id,
        business_id=business_id,
        entry_type="reversed",
        amount_delta=-money(original.amount_delta),
        units_delta=-int(original.units_delta),
        request_key=request_key,
        visit_id=original.visit_id,
        redeems_entry_id=original.id,
        actor_user_id=actor_user_id,
        reason=(reason or "")[:255] or None,
    )
    db.add(entry)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        existing = (
            db.query(GiftLedgerEntry)
            .filter(
                GiftLedgerEntry.certificate_id == certificate.id,
                GiftLedgerEntry.redeems_entry_id == original.id,
            )
            .first()
        )
        if existing is None:
            raise GiftError("gift_request_conflict")
        db.commit()
        return certificate, existing
    _refresh_status(db, certificate)
    db.commit()
    db.refresh(entry)
    db.refresh(certificate)
    return certificate, entry


def list_certificates(
    db: Session,
    business_id: int,
    *,
    status: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
) -> list[GiftCertificate]:
    moment = _now()
    query = db.query(GiftCertificate).filter(GiftCertificate.business_id == business_id)
    if status:
        # The stored status may lag behind a redemption or expiration. Filter by
        # the same balances as _refresh_status before applying the page boundary.
        amount = select(func.coalesce(func.sum(GiftLedgerEntry.amount_delta), 0)).where(
            GiftLedgerEntry.certificate_id == GiftCertificate.id,
        ).scalar_subquery()
        units = select(func.coalesce(func.sum(GiftLedgerEntry.units_delta), 0)).where(
            GiftLedgerEntry.certificate_id == GiftCertificate.id,
        ).scalar_subquery()
        exhausted = case((GiftCertificate.kind == "money", amount <= 0), else_=units <= 0)
        live_status = case(
            (or_(GiftCertificate.status == EXPIRED_STATUS, GiftCertificate.expires_at <= moment), EXPIRED_STATUS),
            (exhausted, EXHAUSTED_STATUS),
            else_=ACTIVE_STATUS,
        )
        query = query.filter(live_status == status)
    certificates = (query.order_by(GiftCertificate.id.desc())
                    .offset(max(0, offset)).limit(max(1, min(limit, 200))).all())
    changed = False
    for certificate in certificates:
        if certificate.status != EXPIRED_STATUS:
            _refresh_status(db, certificate, moment)
            changed = True
    if changed:
        db.commit()
    return certificates


def get_certificate(db: Session, business_id: int, certificate_id: int) -> GiftCertificate:
    certificate = (
        db.query(GiftCertificate)
        .filter(
            GiftCertificate.id == certificate_id,
            GiftCertificate.business_id == business_id,
        )
        .first()
    )
    if certificate is None:
        raise GiftNotFound()
    if certificate.status != EXPIRED_STATUS:
        _refresh_status(db, certificate)
        db.commit()
    return certificate


def lookup_certificate(db: Session, business_id: int, code: str) -> GiftCertificate:
    certificate = (
        db.query(GiftCertificate)
        .filter(
            GiftCertificate.business_id == business_id,
            GiftCertificate.code_hash == code_digest(code),
        )
        .first()
    )
    if certificate is None:
        raise GiftNotFound()
    if certificate.status != EXPIRED_STATUS:
        locked = (
            db.query(GiftCertificate)
            .filter(GiftCertificate.id == certificate.id)
            .populate_existing()
            .with_for_update()
            .one()
        )
        _refresh_status(db, locked)
        db.commit()
        certificate = locked
    return certificate
