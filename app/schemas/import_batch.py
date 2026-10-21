"""Contract for the owner-facing CRM import wizard.

The wizard deliberately has explicit steps: upload, mapping, preview, confirmation.
A preview is always computed from stored rows and never writes to clients or visits,
and a confirmation must name the exact source hash, mapping version and preview it
was prepared from.
"""
from datetime import date, datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator

from app.schemas.base import InputModel
from app.schemas.datetimes import UtcDatetime
from app.schemas.errors import invalid_choice

DuplicatePolicy = Literal["skip", "update", "create"]
ImportStatus = Literal[
    "uploaded",
    "mapped",
    "previewed",
    "importing",
    "completed",
    "completed_with_errors",
    "failed",
]
RowStatus = Literal["pending", "valid", "error", "imported", "skipped", "failed"]

MAPPING_TARGETS = (
    "full_name",
    "phone",
    "email",
    "birth_date",
    "gender",
    "language",
    "visit_date",
    "visit_service",
    "visit_specialist",
    "visit_price",
    "visit_status",
    "visit_payment_method",
)
VISIT_TARGETS = tuple(target for target in MAPPING_TARGETS if target.startswith("visit_"))


class ImportRowClient(BaseModel):
    full_name: str
    phone: Optional[str] = None
    email: Optional[str] = None
    birth_date: Optional[date] = None
    gender: Optional[str] = None
    language: Optional[str] = None


class ImportRowVisit(BaseModel):
    date_start: UtcDatetime
    service_id: Optional[int] = None
    service_name: Optional[str] = None
    specialist_id: int
    specialist_name: Optional[str] = None
    price: Optional[float] = None
    status: str
    payment_method: Optional[str] = None
    creates_service: bool = False


class ImportRowOut(BaseModel):
    row_number: int
    status: RowStatus
    action: Optional[str] = None
    error_code: Optional[str] = None
    error_detail: Optional[str] = None
    client_id: Optional[int] = None
    visit_id: Optional[int] = None
    client: Optional[ImportRowClient] = None
    visit: Optional[ImportRowVisit] = None


class ImportBatchOut(BaseModel):
    id: int
    status: ImportStatus
    source_filename: str
    source_hash: str
    content_type: Optional[str] = None
    headers: list[str]
    column_signature: str
    mapping: Optional[dict[str, str]] = None
    suggested_mapping: dict[str, str] = Field(default_factory=dict)
    mapping_version: int
    preview_version: int
    duplicate_policy: DuplicatePolicy
    row_count: int
    valid_count: int
    error_count: int
    create_count: int
    update_count: int
    skip_count: int
    created_count: int
    updated_count: int
    skipped_count: int
    failed_count: int = 0
    last_checkpoint: int
    timezone: str
    currency: str
    created_at: UtcDatetime
    updated_at: UtcDatetime
    previewed_at: Optional[UtcDatetime] = None
    confirmed_at: Optional[UtcDatetime] = None
    finished_at: Optional[UtcDatetime] = None
    source_expires_at: Optional[UtcDatetime] = None


class ImportMappingIn(InputModel):
    mapping: dict[str, str] = Field(..., max_length=len(MAPPING_TARGETS))

    @field_validator("mapping")
    @classmethod
    def known_targets(cls, value):
        unknown = sorted(set(value) - set(MAPPING_TARGETS))
        if unknown:
            raise invalid_choice(list(MAPPING_TARGETS), "Mapping field")
        return value


class ImportPreviewIn(InputModel):
    duplicate_policy: DuplicatePolicy = "skip"


class ImportConfirmIn(InputModel):
    source_hash: str = Field(..., min_length=64, max_length=64, pattern=r"^[a-f0-9]{64}$")
    mapping_version: int = Field(..., ge=1)
    preview_version: int = Field(..., ge=1)
    duplicate_policy: DuplicatePolicy


class ImportSummary(BaseModel):
    """Result of a confirmation call; repeated calls return the same stored state."""

    batch: ImportBatchOut
    already_completed: bool = False


def visit_preview_from_json(value: Optional[dict]) -> Optional[ImportRowVisit]:
    if not isinstance(value, dict) or not value.get("date_start"):
        return None
    try:
        start = datetime.fromisoformat(str(value["date_start"]))
    except ValueError:
        return None
    return ImportRowVisit(
        date_start=start,
        service_id=value.get("service_id"),
        service_name=value.get("service_name"),
        specialist_id=value.get("specialist_id") or 0,
        specialist_name=value.get("specialist_name"),
        price=value.get("price"),
        status=value.get("status") or "completed",
        payment_method=value.get("payment_method"),
        creates_service=bool(value.get("creates_service")),
    )
