"""Marketing consent, audiences, campaign drafts, confirmation and dispatch.

A campaign can only reach clients who explicitly opted in for this business. The audience and
the text are snapshotted at confirmation; dispatch checks the consent again, so a client who
unsubscribed after confirmation is not mailed. Delivery itself stays in the shared outbox.
"""
import hashlib
import hmac
import json
from datetime import datetime, timedelta
from typing import Optional

from fastapi import HTTPException
from sqlalchemy import and_, func, or_
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.api.core import notification_preferences
from app.api.core.clock import utcnow
from app.api.core.config import settings
from app.api.core.i18n import _
from app.api.core.notification_outbox import enqueue_notification
from app.db.models.campaign import Campaign
from app.db.models.client import Client
from app.db.models.client_business_relation import ClientBusinessRelation
from app.db.models.marketing_consent import MarketingConsent
from app.db.models.notification_outbox import NotificationOutbox
from app.db.models.user import User
from app.db.models.visit import Visit

KIND = "campaign"
SEGMENT_NO_VISITS = "no_visits"
MAX_RECIPIENTS = 500
PREVIEW_RECIPIENTS = 20
LANGUAGES = ("en", "ru", "cz")
COUNTS = ("pending", "processing", "sent", "captured", "skipped", "failed", "cancelled")


def _canonical(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _hash(value) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def mask_email(value: str) -> str:
    local, at, domain = (value or "").partition("@")
    if not at:
        return f"{value[:1]}***" if value else "***"
    return f"{local[:1]}***@{domain}"


def record_consent(
    db: Session,
    *,
    business_id: int,
    client_id: int,
    source: str,
    source_ref: Optional[str] = None,
    channel: str = "email",
    now: Optional[datetime] = None,
) -> MarketingConsent:
    """Explicit opt-in only. Repeating it after a revocation subscribes the client again."""
    now = now or utcnow()
    values = {
        "business_id": business_id,
        "client_id": client_id,
        "channel": channel,
        "status": "subscribed",
        "source": source,
        "source_ref": source_ref,
        "subscribed_at": now,
        "revoked_at": None,
        "revoked_source": None,
        "revoked_campaign_id": None,
        "created_at": now,
        "updated_at": now,
    }
    stmt = (
        pg_insert(MarketingConsent)
        .values(**values)
        .on_conflict_do_update(
            index_elements=[
                MarketingConsent.business_id,
                MarketingConsent.client_id,
                MarketingConsent.channel,
            ],
            set_={
                "status": "subscribed",
                "source": source,
                "source_ref": source_ref,
                "subscribed_at": now,
                "revoked_at": None,
                "revoked_source": None,
                "revoked_campaign_id": None,
                "updated_at": now,
            },
        )
    )
    db.execute(stmt)
    return (
        db.query(MarketingConsent)
        .filter(
            MarketingConsent.business_id == business_id,
            MarketingConsent.client_id == client_id,
            MarketingConsent.channel == channel,
        )
        .one()
    )


def revoke_consent(
    db: Session,
    consent: MarketingConsent,
    *,
    source: str,
    campaign_id: Optional[int] = None,
    now: Optional[datetime] = None,
) -> MarketingConsent:
    if consent.status == "unsubscribed":
        return consent
    now = now or utcnow()
    consent.status = "unsubscribed"
    consent.revoked_at = now
    consent.revoked_source = source
    consent.revoked_campaign_id = campaign_id
    consent.updated_at = now
    db.flush()
    return consent


def unsubscribe_token(consent_id: int, campaign_id: int) -> str:
    payload = f"campaign-unsubscribe:{consent_id}:{campaign_id}"
    signature = hmac.new(
        settings.jwt_secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    return f"{consent_id}.{campaign_id}.{signature}"


def parse_unsubscribe_token(token: str) -> tuple[int, int]:
    try:
        consent_part, campaign_part, signature = (token or "").split(".", 2)
        consent_id = int(consent_part)
        campaign_id = int(campaign_part)
    except (TypeError, ValueError):
        raise ValueError("invalid token")
    if consent_id <= 0 or campaign_id <= 0:
        raise ValueError("invalid token")
    expected = unsubscribe_token(consent_id, campaign_id)
    if not hmac.compare_digest(expected, f"{consent_id}.{campaign_id}.{signature}"):
        raise ValueError("invalid signature")
    return consent_id, campaign_id


def consent_from_token(db: Session, locale: str, token: str) -> tuple[MarketingConsent, Optional[Campaign]]:
    try:
        consent_id, campaign_id = parse_unsubscribe_token(token)
    except ValueError:
        raise HTTPException(status_code=400, detail=_("invalid_unsubscribe_token", locale))
    consent = db.get(MarketingConsent, consent_id)
    if consent is None:
        raise HTTPException(status_code=400, detail=_("invalid_unsubscribe_token", locale))
    campaign = db.get(Campaign, campaign_id)
    if campaign is not None and campaign.business_id != consent.business_id:
        raise HTTPException(status_code=400, detail=_("invalid_unsubscribe_token", locale))
    return consent, campaign


def unsubscribe_by_token(db: Session, locale: str, token: str) -> MarketingConsent:
    consent, campaign = consent_from_token(db, locale, token)
    return revoke_consent(
        db,
        consent,
        source="unsubscribe_link",
        campaign_id=campaign.id if campaign is not None else None,
    )


def segment_recipients(
    db: Session,
    business_id: int,
    inactive_days: int,
    now: Optional[datetime] = None,
) -> list[dict]:
    """Consenting clients of this business with no completed visit for ``inactive_days``.

    Cancelled and planned visits do not count as activity; the relation creation date does,
    so a brand-new card does not immediately receive a "we miss you" campaign.
    """
    now = now or utcnow()
    cutoff = now - timedelta(days=inactive_days)
    last_visit = (
        db.query(
            Visit.client_id.label("client_id"),
            func.max(Visit.date_start).label("last_visit_at"),
        )
        .filter(
            Visit.business_id == business_id,
            Visit.status == "completed",
            Visit.deleted_at.is_(None),
            Visit.date_start <= now,
        )
        .group_by(Visit.client_id)
        .subquery()
    )
    rows = (
        db.query(
            MarketingConsent,
            Client,
            ClientBusinessRelation.created_at,
            last_visit.c.last_visit_at,
        )
        .join(Client, Client.id == MarketingConsent.client_id)
        .join(
            ClientBusinessRelation,
            and_(
                ClientBusinessRelation.client_id == MarketingConsent.client_id,
                ClientBusinessRelation.business_id == MarketingConsent.business_id,
            ),
        )
        .outerjoin(last_visit, last_visit.c.client_id == Client.id)
        .filter(
            MarketingConsent.business_id == business_id,
            MarketingConsent.channel == "email",
            MarketingConsent.status == "subscribed",
            Client.deleted_at.is_(None),
            Client.archived_at.is_(None),
            Client.contact_email.isnot(None),
            func.length(func.trim(Client.contact_email)) > 0,
            or_(
                last_visit.c.last_visit_at <= cutoff,
                and_(
                    last_visit.c.last_visit_at.is_(None),
                    ClientBusinessRelation.created_at <= cutoff,
                ),
            ),
        )
        .all()
    )

    recipients: dict[str, dict] = {}
    for consent, client, relation_created_at, last_visit_at in rows:
        activity = last_visit_at or relation_created_at
        email = (client.contact_email or "").strip()
        if not email or activity is None:
            continue
        # one mailbox must receive one message even if two client cards share it
        key = email.lower()
        candidate = {
            "consent_id": consent.id,
            "client_id": client.id,
            "email": email,
            "name": client.full_name,
            "language": client.language if client.language in LANGUAGES else "en",
            "last_visit_at": last_visit_at.isoformat() if last_visit_at else None,
            "last_activity_at": activity.isoformat(),
        }
        existing = recipients.get(key)
        if existing is None or candidate["last_activity_at"] > existing["last_activity_at"]:
            recipients[key] = candidate
    return sorted(
        recipients.values(),
        key=lambda item: (item["last_activity_at"], item["client_id"]),
    )


def audience_hash(recipients: list[dict]) -> str:
    return _hash(
        [
            {"client_id": item["client_id"], "email": item["email"].lower()}
            for item in sorted(recipients, key=lambda item: item["client_id"])
        ]
    )


def content_hash(campaign: Campaign) -> str:
    return _hash(
        {
            "business_id": campaign.business_id,
            "subject": campaign.subject,
            "body": campaign.body,
            "segment_kind": campaign.segment_kind,
            "inactive_days": campaign.inactive_days,
            "scheduled_at": campaign.scheduled_at.isoformat() if campaign.scheduled_at else None,
        }
    )


def send_readiness(db: Session, business_id: int) -> tuple[bool, bool]:
    """(provider_ready, notifications_enabled) for this business."""
    row = notification_preferences.get_settings(db, business_id)
    provider_ready = notification_preferences.channel_ready(row.channel if row else "email")
    return provider_ready, bool(row and row.is_enabled)


def create_campaign(db: Session, business_id: int, user_id: int, payload) -> Campaign:
    campaign = Campaign(
        business_id=business_id,
        name=payload.name,
        subject=payload.subject,
        body=payload.body,
        segment_kind=payload.segment_kind,
        inactive_days=payload.inactive_days,
        scheduled_at=payload.scheduled_at,
        created_by=user_id,
    )
    db.add(campaign)
    db.flush()
    return campaign


def _clear_confirmation(campaign: Campaign) -> None:
    campaign.audience_snapshot = None
    campaign.audience_hash = None
    campaign.content_hash = None
    campaign.recipient_count = 0
    campaign.queued_count = 0
    campaign.confirmed_at = None
    campaign.confirmed_by = None


def _lock_campaign(db: Session, campaign: Campaign) -> Campaign:
    # Endpoint lookups can be stale by the time a transition starts.
    with db.no_autoflush:
        return db.query(Campaign).filter(
            Campaign.id == campaign.id, Campaign.business_id == campaign.business_id,
        ).populate_existing().with_for_update().one()


def update_campaign(db: Session, campaign: Campaign, fields: dict, locale: str) -> Campaign:
    campaign = _lock_campaign(db, campaign)
    if campaign.status not in ("draft", "confirmed"):
        raise HTTPException(status_code=409, detail=_("campaign_locked", locale))
    for key, value in fields.items():
        setattr(campaign, key, value)
    if campaign.status == "confirmed":
        # the confirmed text/audience no longer describes this campaign
        _clear_confirmation(campaign)
        campaign.status = "draft"
    db.flush()
    return campaign


def confirm_campaign(
    db: Session,
    campaign: Campaign,
    user: User,
    locale: str,
    now: Optional[datetime] = None,
    seen: Optional[tuple[str, str]] = None,
) -> Campaign:
    campaign = _lock_campaign(db, campaign)
    if campaign.status not in ("draft", "confirmed"):
        raise HTTPException(status_code=409, detail=_("campaign_locked", locale))
    now = now or utcnow()
    recipients = segment_recipients(db, campaign.business_id, campaign.inactive_days, now)
    if not recipients:
        raise HTTPException(status_code=409, detail=_("campaign_empty_audience", locale))
    if len(recipients) > MAX_RECIPIENTS:
        raise HTTPException(status_code=409, detail=_("campaign_too_many_recipients", locale))
    new_audience, new_content = audience_hash(recipients), content_hash(campaign)
    # confirm only what was on the owner's screen; a text edit or a new client since then means a new preview
    if seen is not None and seen != (new_audience, new_content):
        raise HTTPException(status_code=409, detail=_("campaign_preview_stale", locale))
    campaign.audience_snapshot = recipients
    campaign.audience_hash = new_audience
    campaign.content_hash = new_content
    campaign.recipient_count = len(recipients)
    campaign.queued_count = 0
    campaign.confirmed_at = now
    campaign.confirmed_by = user.id
    campaign.status = "confirmed"
    campaign.updated_at = now
    db.flush()
    return campaign


def preview_campaign(
    db: Session,
    campaign: Campaign,
    locale: str,
    now: Optional[datetime] = None,
) -> dict:
    frozen = campaign.audience_snapshot is not None and campaign.status != "draft"
    if frozen:
        recipients = campaign.audience_snapshot or []
        source = "frozen"
        consent_ids = [item.get("consent_id") for item in recipients if item.get("consent_id")]
        active_ids = (
            {
                row[0]
                for row in db.query(MarketingConsent.id)
                .filter(
                    MarketingConsent.id.in_(consent_ids),
                    MarketingConsent.business_id == campaign.business_id,
                    MarketingConsent.status == "subscribed",
                )
                .all()
            }
            if consent_ids
            else set()
        )
        active_count = sum(
            1 for item in recipients if item.get("consent_id") in active_ids
        )
    else:
        recipients = segment_recipients(
            db, campaign.business_id, campaign.inactive_days, now
        )
        source = "draft"
        active_count = len(recipients)
    provider_ready, notifications_enabled = send_readiness(db, campaign.business_id)
    total = len(recipients)
    sample = [
        {
            "client_id": item["client_id"],
            "name": item["name"],
            "email": mask_email(item["email"]),
            "last_visit_at": item.get("last_visit_at"),
        }
        for item in recipients[:PREVIEW_RECIPIENTS]
    ]
    return {
        "campaign_id": campaign.id,
        "status": campaign.status,
        "audience_source": source,
        "audience_hash": campaign.audience_hash if frozen else audience_hash(recipients),
        "content_hash": campaign.content_hash if frozen else content_hash(campaign),
        "recipient_count": total,
        "limit": MAX_RECIPIENTS,
        "over_limit": total > MAX_RECIPIENTS,
        "recipients": sample,
        "provider_ready": provider_ready,
        "notifications_enabled": notifications_enabled,
        "requires_confirmation": campaign.status == "draft",
        "send_ready": (
            campaign.status == "confirmed"
            and provider_ready
            and notifications_enabled
            and 0 < active_count <= MAX_RECIPIENTS
        ),
        "excluded_after_confirmation": max(0, total - active_count),
    }


def dispatch_campaign(
    db: Session,
    campaign: Campaign,
    locale: str,
    now: Optional[datetime] = None,
) -> Campaign:
    campaign = _lock_campaign(db, campaign)
    if campaign.status == "draft":
        raise HTTPException(status_code=409, detail=_("campaign_not_confirmed", locale))
    if campaign.status in ("sent", "cancelled"):
        raise HTTPException(status_code=409, detail=_("campaign_not_sendable", locale))
    if campaign.status == "sending":
        return campaign

    if not campaign.content_hash or campaign.content_hash != content_hash(campaign):
        raise HTTPException(status_code=409, detail=_("campaign_preview_stale", locale))

    provider_ready, notifications_enabled = send_readiness(db, campaign.business_id)
    if not notifications_enabled:
        raise HTTPException(
            status_code=409, detail=_("campaign_notifications_disabled", locale)
        )
    if not provider_ready:
        raise HTTPException(status_code=409, detail=_("campaign_provider_not_ready", locale))
    channel = notification_preferences.enabled_channel(db, campaign.business_id)
    if channel is None:
        raise HTTPException(status_code=409, detail=_("campaign_provider_not_ready", locale))

    now = now or utcnow()
    snapshot = campaign.audience_snapshot or []
    consent_ids = [item.get("consent_id") for item in snapshot if item.get("consent_id")]
    live = {
        row.id: row
        for row in (
            db.query(MarketingConsent)
            .filter(
                MarketingConsent.id.in_(consent_ids),
                MarketingConsent.business_id == campaign.business_id,
                MarketingConsent.status == "subscribed",
            )
            .all()
            if consent_ids
            else []
        )
    }
    queued = 0
    scheduled_at = campaign.scheduled_at or now
    for item in snapshot:
        consent = live.get(item.get("consent_id"))
        if consent is None or consent.client_id != item["client_id"]:
            # revoked after confirmation: compliance wins over the frozen audience
            continue
        enqueue_notification(
            db,
            event_key=f"{KIND}:{campaign.id}:{item['client_id']}",
            kind=KIND,
            recipient=item["email"],
            business_id=campaign.business_id,
            channel=channel,
            language=item.get("language"),
            campaign_id=campaign.id,
            scheduled_at=scheduled_at,
            payload={
                "campaign_id": campaign.id,
                "consent_id": consent.id,
                "client_id": item["client_id"],
                "client_name": item["name"],
                "subject": campaign.subject,
                "body": campaign.body,
            },
        )
        queued += 1
    if queued == 0:
        raise HTTPException(status_code=409, detail=_("campaign_empty_audience", locale))
    campaign.queued_count = queued
    campaign.status = "sending"
    campaign.updated_at = now
    db.flush()
    return campaign


def cancel_campaign(
    db: Session,
    campaign: Campaign,
    locale: str,
    now: Optional[datetime] = None,
) -> Campaign:
    campaign = _lock_campaign(db, campaign)
    if campaign.status == "sent":
        raise HTTPException(status_code=409, detail=_("campaign_already_sent", locale))
    if campaign.status == "cancelled":
        return campaign
    now = now or utcnow()
    (
        db.query(NotificationOutbox)
        .filter(
            NotificationOutbox.campaign_id == campaign.id,
            NotificationOutbox.status.in_(("pending", "processing")),
            NotificationOutbox.provider_message_id.is_(None),
        )
        .update(
            {
                NotificationOutbox.status: "cancelled",
                NotificationOutbox.last_error: "campaign_cancelled",
                NotificationOutbox.locked_until: None,
                NotificationOutbox.updated_at: now,
            },
            synchronize_session=False,
        )
    )
    campaign.status = "cancelled"
    campaign.updated_at = now
    db.flush()
    return campaign


def campaign_stats(
    db: Session,
    campaign: Campaign,
    now: Optional[datetime] = None,
) -> dict:
    campaign = _lock_campaign(db, campaign)
    rows = (
        db.query(NotificationOutbox.status, func.count(NotificationOutbox.id))
        .filter(NotificationOutbox.campaign_id == campaign.id)
        .group_by(NotificationOutbox.status)
        .all()
    )
    counts = {status: 0 for status in COUNTS}
    for status, count in rows:
        if status in counts:
            counts[status] = count
    unsubscribed = (
        db.query(func.count(MarketingConsent.id))
        .filter(
            MarketingConsent.business_id == campaign.business_id,
            MarketingConsent.revoked_campaign_id == campaign.id,
            MarketingConsent.status == "unsubscribed",
        )
        .scalar()
        or 0
    )
    if (
        campaign.status == "sending"
        and counts["pending"] == 0
        and counts["processing"] == 0
    ):
        campaign.status = "sent"
        campaign.updated_at = now or utcnow()
        db.flush()
    return {
        "campaign_id": campaign.id,
        "status": campaign.status,
        "audience": campaign.recipient_count,
        "queued": campaign.queued_count,
        "excluded_after_confirmation": max(0, campaign.recipient_count - campaign.queued_count),
        "pending": counts["pending"],
        "processing": counts["processing"],
        "sent": counts["sent"],
        "delivered": counts["sent"],
        "captured": counts["captured"],
        "skipped": counts["skipped"],
        "failed": counts["failed"],
        "errors": counts["failed"],
        "cancelled": counts["cancelled"],
        "unsubscribed": unsubscribed,
        "opens_tracked": False,
        "revenue_tracked": False,
    }


def campaign_delivery_block(
    db: Session, row: NotificationOutbox
) -> Optional[str]:
    """Why a queued campaign message must not leave the outbox, or None when it may."""
    if row.campaign_id is None:
        return "campaign_missing"
    campaign = db.get(Campaign, row.campaign_id)
    if campaign is None:
        return "campaign_missing"
    if campaign.status == "cancelled":
        return "campaign_cancelled"
    if not campaign.content_hash or campaign.content_hash != content_hash(campaign):
        return "campaign_content_changed"
    payload = row.payload or {}
    consent = (
        db.get(MarketingConsent, payload.get("consent_id"))
        if payload.get("consent_id")
        else None
    )
    if (
        consent is None
        or consent.status != "subscribed"
        or consent.business_id != row.business_id
        or consent.client_id != payload.get("client_id")
    ):
        return "unsubscribed"
    return None


def campaign_for_business(
    db: Session, campaign_id: int, business_id: int, locale: str
) -> Campaign:
    campaign = (
        db.query(Campaign)
        .filter(Campaign.id == campaign_id, Campaign.business_id == business_id)
        .first()
    )
    if campaign is None:
        raise HTTPException(status_code=404, detail=_("campaign_not_found", locale))
    return campaign
