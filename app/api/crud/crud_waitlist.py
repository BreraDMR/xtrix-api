"""Waitlist persistence helpers: digest-only tokens, dedupe keys and lazy expiry."""
import hashlib
import secrets
from datetime import datetime
from typing import Optional

from sqlalchemy.orm import Session

from app.api.core.clock import utcnow
from app.db.models.notification_outbox import NotificationOutbox
from app.db.models.waitlist import WaitlistEntry, WaitlistOffer

TOKEN_BYTES = 32


def hash_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def new_token() -> str:
    return secrets.token_urlsafe(TOKEN_BYTES)


def dedupe_key(
    business_id: int,
    email: str,
    service_id: int,
    specialist_id: Optional[int],
    date_from,
    date_to,
    time_preference: str,
) -> str:
    raw = "|".join(
        (
            str(business_id),
            email.strip().lower(),
            str(service_id),
            str(specialist_id or 0),
            date_from.isoformat(),
            date_to.isoformat() if date_to is not None else "*",
            time_preference,
        )
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def issue_entry_token(entry: WaitlistEntry) -> str:
    raw = new_token()
    entry.token_hash = hash_token(raw)
    return raw


def issue_offer_token(offer: WaitlistOffer) -> str:
    raw = new_token()
    offer.token_hash = hash_token(raw)
    return raw


def find_active_entry(db: Session, business_id: int, key: str) -> Optional[WaitlistEntry]:
    return (
        db.query(WaitlistEntry)
        .filter(
            WaitlistEntry.business_id == business_id,
            WaitlistEntry.dedupe_key == key,
            WaitlistEntry.status == "active",
        )
        .first()
    )


def entry_by_token(
    db: Session, raw_token: Optional[str], lock: bool = False
) -> Optional[WaitlistEntry]:
    if not raw_token or len(raw_token) > 128:
        return None
    query = db.query(WaitlistEntry).filter(
        WaitlistEntry.token_hash == hash_token(raw_token)
    )
    if lock:
        query = query.populate_existing().with_for_update()
    return query.first()


def offer_by_token(
    db: Session, raw_token: Optional[str], lock: bool = False
) -> Optional[WaitlistOffer]:
    if not raw_token or len(raw_token) > 128:
        return None
    query = db.query(WaitlistOffer).filter(
        WaitlistOffer.token_hash == hash_token(raw_token)
    )
    if lock:
        query = query.populate_existing().with_for_update()
    return query.first()


def cancel_offer_notifications(db: Session, offer_ids: list[int], now: datetime) -> None:
    if not offer_ids:
        return
    keys = [f"waitlist_offer:{offer_id}" for offer_id in offer_ids]
    db.query(NotificationOutbox).filter(
        NotificationOutbox.event_key.in_(keys),
        NotificationOutbox.status == "pending",
    ).update(
        {NotificationOutbox.status: "cancelled", NotificationOutbox.updated_at: now},
        synchronize_session=False,
    )


def expire_pending_offers(
    db: Session, now: Optional[datetime] = None, business_id: Optional[int] = None
) -> int:
    """Close offers whose time is up and drop their unsent mail; entries stay active."""
    now = now or utcnow()
    query = db.query(WaitlistOffer).filter(
        WaitlistOffer.status == "pending",
        WaitlistOffer.expires_at <= now,
    )
    if business_id is not None:
        query = query.filter(WaitlistOffer.business_id == business_id)
    offers = query.with_for_update(skip_locked=True).all()
    if not offers:
        return 0
    for offer in offers:
        offer.status = "expired"
        offer.updated_at = now
    cancel_offer_notifications(db, [offer.id for offer in offers], now)
    db.commit()
    return len(offers)
