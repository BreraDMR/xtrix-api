"""Ordinary client notes, tags and preferences.

These are CRM fields the business keeps for itself. Medical answers live in the
questionnaire package and never flow into these models.
"""
import re
from typing import Annotated, List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.base import InputModel
from app.schemas.datetimes import UtcDatetime
from app.schemas.errors import invalid

MAX_NOTE_LENGTH = 2000
MAX_PREFERENCES_LENGTH = 2000
MAX_TAGS = 20
MAX_TAG_LENGTH = 40

_HTML_TAG = re.compile(r"<\s*/?\s*[a-zA-Z][^>]*>")
_CONTROL_CHARACTER = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
_WHITESPACE = re.compile(r"\s+")

ClientTag = Annotated[str, Field(min_length=1, max_length=MAX_TAG_LENGTH)]


def plain_client_text(value: str, what: str = "text") -> str:
    if _HTML_TAG.search(value):
        raise invalid("html_not_allowed", "HTML is not allowed in {what}", what=what)
    if _CONTROL_CHARACTER.search(value):
        raise invalid("control_characters_not_allowed", "{what} contains control characters", what=what)
    return value


class ClientNoteCreate(InputModel):
    body: str = Field(..., min_length=1, max_length=MAX_NOTE_LENGTH, description="Plain text note")
    model_config = ConfigDict(extra="forbid")

    @field_validator("body")
    @classmethod
    def validate_body(cls, value: str) -> str:
        return plain_client_text(value, "note")


class ClientNoteUpdate(InputModel):
    body: str = Field(..., min_length=1, max_length=MAX_NOTE_LENGTH, description="Plain text note")
    model_config = ConfigDict(extra="forbid")

    @field_validator("body")
    @classmethod
    def validate_body(cls, value: str) -> str:
        return plain_client_text(value, "note")


class ClientNoteOut(BaseModel):
    id: int
    client_id: int
    business_id: int
    author_id: Optional[int] = None
    author_name: Optional[str] = None
    body: str
    created_at: UtcDatetime
    updated_at: UtcDatetime
    model_config = ConfigDict(from_attributes=True)


class ClientProfileUpdate(InputModel):
    tags: Optional[List[ClientTag]] = Field(None, max_length=MAX_TAGS)
    preferences: Optional[str] = Field(None, max_length=MAX_PREFERENCES_LENGTH)
    model_config = ConfigDict(extra="forbid")

    @field_validator("tags")
    @classmethod
    def validate_tags(cls, value: Optional[List[str]]) -> Optional[List[str]]:
        if value is None:
            return value
        result: List[str] = []
        seen: set[str] = set()
        for tag in value:
            cleaned = _WHITESPACE.sub(" ", tag).strip()
            if not cleaned:
                raise invalid("empty_tag", "Tags cannot be empty")
            plain_client_text(cleaned, "tag")
            key = cleaned.casefold()
            if key in seen:
                raise invalid("duplicate_tag", "Tags must be unique", tag=cleaned)
            seen.add(key)
            result.append(cleaned)
        return result

    @field_validator("preferences")
    @classmethod
    def validate_preferences(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return value
        cleaned = value.strip()
        if not cleaned:
            return None
        return plain_client_text(cleaned, "preferences")


class ClientProfileOut(BaseModel):
    client_id: int
    tags: List[str] = Field(default_factory=list)
    preferences: Optional[str] = None
    model_config = ConfigDict(from_attributes=True)
