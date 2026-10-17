"""One datetime type for every API schema.

The database keeps naive UTC (see app/api/core/clock.py). On the way in, a time with an offset
is converted to UTC instead of having the offset silently dropped, and a naive time keeps meaning
UTC, like before. On the way out every datetime is ISO with a trailing Z - without it browsers
read the value as local time and show it shifted by the user's UTC offset.
"""
from datetime import datetime, timezone
from typing import Annotated

from pydantic import AfterValidator, PlainSerializer, WithJsonSchema


def to_naive_utc(value: datetime) -> datetime:
    if value.tzinfo is not None:
        value = value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


def utc_iso(value: datetime) -> str:
    return to_naive_utc(value).isoformat() + "Z"


UtcDatetime = Annotated[
    datetime,
    AfterValidator(to_naive_utc),
    PlainSerializer(utc_iso, return_type=str, when_used="json"),
    # same schema for input and output, so the contract keeps plain date-time fields
    # and FastAPI doesn't split models into -Input/-Output twins
    WithJsonSchema({"type": "string", "format": "date-time"}),
]
