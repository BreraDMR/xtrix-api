"""Contracts for explicit marketing consent and owner-confirmed campaigns."""
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.base import InputModel
from app.schemas.datetimes import UtcDatetime
from app.schemas.errors import invalid

CAMPAIGN_STATUSES = ("draft", "confirmed", "sending", "sent", "cancelled")


class CampaignIn(InputModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    subject: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1, max_length=10000)
    segment_kind: Literal["no_visits"] = "no_visits"
    inactive_days: int = Field(90, ge=1, le=3650)
    scheduled_at: Optional[UtcDatetime] = None

    @model_validator(mode="after")
    def body_has_text(self):
        if not self.body.strip():
            raise invalid("campaign_body_required", "Write the message text")
        return self


class CampaignUpdate(InputModel):
    model_config = ConfigDict(extra="forbid")

    name: Optional[str] = Field(None, min_length=1, max_length=120)
    subject: Optional[str] = Field(None, min_length=1, max_length=200)
    body: Optional[str] = Field(None, min_length=1, max_length=10000)
    segment_kind: Optional[Literal["no_visits"]] = None
    inactive_days: Optional[int] = Field(None, ge=1, le=3650)
    scheduled_at: Optional[UtcDatetime] = None

    @model_validator(mode="after")
    def body_has_text(self):
        for field in ("name", "subject", "body", "segment_kind", "inactive_days"):
            if field in self.model_fields_set and getattr(self, field) is None:
                raise invalid("campaign_field_required", "This field cannot be null")
        if self.body is not None and not self.body.strip():
            raise invalid("campaign_body_required", "Write the message text")
        return self


class CampaignOut(BaseModel):
    id: int
    business_id: int
    name: str
    subject: str
    body: str
    segment_kind: str
    inactive_days: int
    scheduled_at: Optional[UtcDatetime] = None
    status: str
    audience_hash: Optional[str] = None
    content_hash: Optional[str] = None
    recipient_count: int
    queued_count: int
    confirmed_at: Optional[UtcDatetime] = None
    confirmed_by: Optional[int] = None
    created_by: Optional[int] = None
    created_at: UtcDatetime
    updated_at: UtcDatetime


class CampaignPreviewRecipient(BaseModel):
    client_id: int
    name: str
    email: str
    last_visit_at: Optional[str] = None


class CampaignPreviewOut(BaseModel):
    campaign_id: int
    status: str
    audience_source: Literal["draft", "frozen"]
    audience_hash: str
    content_hash: str
    recipient_count: int
    limit: int
    over_limit: bool
    recipients: list[CampaignPreviewRecipient] = Field(default_factory=list)
    provider_ready: bool
    notifications_enabled: bool
    requires_confirmation: bool
    send_ready: bool
    excluded_after_confirmation: int


class CampaignStatsOut(BaseModel):
    campaign_id: int
    status: str
    audience: int
    queued: int
    excluded_after_confirmation: int
    pending: int
    processing: int
    sent: int
    delivered: int
    captured: int
    skipped: int
    failed: int
    errors: int
    cancelled: int
    unsubscribed: int
    opens_tracked: bool = False
    revenue_tracked: bool = False


class MarketingConsentOut(BaseModel):
    status: str
    channel: str
    source: Optional[str] = None
    source_ref: Optional[str] = None
    subscribed_at: Optional[UtcDatetime] = None
    revoked_at: Optional[UtcDatetime] = None
    revoked_source: Optional[str] = None


class PublicUnsubscribeOut(BaseModel):
    business_name: str
    email: str
    status: Literal["subscribed", "unsubscribed"]
    channel: str


class UnsubscribeIn(InputModel):
    model_config = ConfigDict(extra="forbid")

    token: str = Field(min_length=8, max_length=500)
