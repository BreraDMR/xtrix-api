"""Public booking contracts.

Everything here is anonymous: the slug is the only tenant key, and every field is an
explicit allowlist entry. Internal ids, staff contacts, buffers and billing stay out.
"""
import re
from typing import Annotated, Optional

from pydantic import AfterValidator, BeforeValidator, BaseModel, ConfigDict, field_validator

from app.api.core.booking_slug import normalize_slug
from app.schemas.base import InputModel
from app.schemas.errors import invalid
from app.schemas.limits import LongText, ShortText


def _checked_slug(value: str) -> str:
    if not (3 <= len(value) <= 64) or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", value):
        raise invalid(
            "invalid_slug",
            "Use 3-64 lowercase letters, digits and single hyphens",
        )
    return value


PublicSlug = Annotated[str, BeforeValidator(normalize_slug), AfterValidator(_checked_slug)]


class PublicBusinessOut(BaseModel):
    """The salon card a guest sees. No business id, no staff contacts, no policy internals."""
    slug: str
    name: str
    business_type: str
    address: Optional[ShortText] = None
    phone: Optional[ShortText] = None
    email: Optional[ShortText] = None
    description: Optional[LongText] = None
    currency: str
    timezone: str


class PublicBookingSettingsOut(BaseModel):
    slug: str
    is_enabled: bool
    model_config = ConfigDict(from_attributes=True)


class PublicBookingSettingsUpdate(InputModel):
    slug: Optional[PublicSlug] = None
    is_enabled: Optional[bool] = None

    @field_validator("is_enabled")
    @classmethod
    def enabled_not_null(cls, value):
        if value is None:
            raise invalid("invalid_choice", "is_enabled cannot be null")
        return value
