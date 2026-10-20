"""Base class for request bodies.

Pydantic is lax by default, and JSON clients trip over that: "   " passes as a name,
true passes as a price of 1, 12345 passes as a date in 1970. Request schemas mix this in
first (class XCreate(InputModel, XBase)) so the Out models built from the database stay as they were.
"""
import types
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any, Literal, Union, get_args, get_origin

from pydantic import BaseModel, ValidationInfo, field_validator

from app.schemas.errors import invalid

# a date outside this is a typo (1800, 9999), not a real visit, expense or birthday
MIN_DATE = date(1900, 1, 1)
MAX_DATE = date(2100, 12, 31)
# secrets are compared byte for byte, trimming them would quietly change them
NO_STRIP = {"password", "new_password", "token", "totp_token", "refresh_token"}


def _kinds(annotation: Any) -> set:
    origin = get_origin(annotation)
    if origin is Annotated:
        return _kinds(get_args(annotation)[0])
    if origin in (Union, types.UnionType):
        return set().union(*(_kinds(a) for a in get_args(annotation)))
    if origin is Literal:
        return {type(a) for a in get_args(annotation)}
    if origin is not None:
        return {origin}
    return {annotation}


class InputModel(BaseModel):
    @field_validator("*", mode="before")
    @classmethod
    def _strict_json_values(cls, value: Any, info: ValidationInfo) -> Any:
        field = cls.model_fields[info.field_name]
        kinds = _kinds(field.annotation)
        if isinstance(value, str) and str in kinds and info.field_name not in NO_STRIP:
            value = value.strip()
            if not value and field.is_required():
                raise invalid("string_too_short", "String should have at least {min_length} character", min_length=1)
        if isinstance(value, bool) and bool not in kinds and kinds & {int, float, Decimal}:
            raise invalid("number_type", "Input should be a number, not true/false")
        if isinstance(value, (int, float)) and not kinds & {int, float, Decimal, bool} and kinds & {date, datetime}:
            raise invalid("date_type", "Input should be a date as an ISO string, like 2026-10-18")
        return value

    @field_validator("*", mode="after")
    @classmethod
    def _sane_dates(cls, value: Any) -> Any:
        if isinstance(value, (date, datetime)):
            day = value.date() if isinstance(value, datetime) else value
            if not MIN_DATE <= day <= MAX_DATE:
                raise invalid("date_out_of_range", "Date should be between {min} and {max}",
                              min=MIN_DATE.isoformat(), max=MAX_DATE.isoformat())
        return value
