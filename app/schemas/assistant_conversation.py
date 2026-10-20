"""Public chat shapes. Ownership always comes from the authenticated session."""
from typing import Annotated, Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

from app.schemas.assistant import ExecuteOut, PlanOut
from app.schemas.datetimes import UtcDatetime

Title = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=160)]
MessageStatus = Literal['pending', 'succeeded', 'failed', 'expired']
ConversationErrorCode = Literal[
    'conversation_not_found', 'conversation_archived', 'request_conflict', 'request_in_progress',
]


class _Input(BaseModel):
    model_config = ConfigDict(extra='forbid')

    @field_validator('title', mode='before', check_fields=False)
    @classmethod
    def clean_title(cls, value):
        if isinstance(value, str):
            return ''.join(char for char in value if char.isprintable()).strip()
        return value


class ConversationCreate(_Input):
    client_request_id: UUID
    title: Optional[Title] = None


class ConversationRename(_Input):
    title: Title


class ConversationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    title: str
    created_at: UtcDatetime
    updated_at: UtcDatetime
    archived_at: Optional[UtcDatetime] = None


class ConversationPage(BaseModel):
    items: list[ConversationOut]
    total: int = Field(ge=0)
    limit: int = Field(default=50, ge=1, le=100)
    offset: int = Field(default=0, ge=0)


class MessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    conversation_id: UUID
    ordinal: int = Field(ge=1)
    request_id: UUID
    role: Literal['user', 'assistant']
    text: str
    status: MessageStatus
    created_at: UtcDatetime
    error_code: Optional[str] = None
    plan: Optional[PlanOut] = None
    result: Optional[ExecuteOut] = None


class MessagePage(BaseModel):
    items: list[MessageOut]
    next_before: Optional[int] = Field(default=None, ge=1)
