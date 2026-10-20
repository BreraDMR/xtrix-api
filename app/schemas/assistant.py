"""Request and response shapes of /assistant/*."""
from typing import Annotated, Literal, Optional, Union
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.ai import plan as p
from app.ai.assistant import MAX_REQUEST_CHARS
from app.schemas.datetimes import UtcDatetime


class AssistantPersonaOut(BaseModel):
    name: str
    gender: Literal["female", "male", "neuter"]
    is_default: bool


class AssistantStatusOut(BaseModel):
    enabled: bool = Field(description="A model provider is configured, so /assistant/plan can work")
    voice_enabled: bool = Field(description="A speech provider is configured, so /assistant/transcribe can work")
    assistant: AssistantPersonaOut
    limit: int = Field(description="Assistant requests per calendar month (UTC) for whoever pays")
    used: int
    resets_at: UtcDatetime


class PlanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_REQUEST_CHARS)]
    locale: Optional[Literal["ru", "cz", "en"]] = Field(
        None, description="Language of the answer; Accept-Language when left out")
    conversation_id: Optional[UUID] = Field(None, description="Active private conversation")
    client_request_id: Optional[UUID] = Field(None, description="Unique client request ID for retry-safety")


class ActionDisplay(BaseModel):
    """Read-only preview resolved by the server within the user's own data; empty for anything else.
    Never phones, e-mails, birth dates or medical data."""
    client_name: Optional[str] = None
    service_name: Optional[str] = None
    specialist_name: Optional[str] = None
    start_time: Optional[UtcDatetime] = None
    end_time: Optional[UtcDatetime] = None
    note: Optional[str] = None


def _with_display(action_cls):
    # the plan action exactly as in app/ai/plan.py plus the preview; not strict on the way out
    return type(f"{action_cls.__name__}Planned", (action_cls,), {
        "__module__": __name__,
        "__annotations__": {"display": ActionDisplay},
        "display": Field(default_factory=ActionDisplay),
        "model_config": ConfigDict(extra="forbid", strict=False),
    })


_PLANNED = [_with_display(cls) for cls in (p.FindClient, p.CreateClient, p.UpdateClient, p.FindFreeSlot,
                                            p.CreateVisit, p.RescheduleVisit, p.CancelVisit, p.AddNote)]
PlannedAction = Annotated[Union[tuple(_PLANNED)], Field(discriminator="action")]


class PlanOut(BaseModel):
    plan_id: str
    summary: str
    questions: list[str]
    actions: list[PlannedAction]
    assistant: AssistantPersonaOut
    expires_at: UtcDatetime


class ExecuteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plan_id: Annotated[str, StringConstraints(min_length=1, max_length=64)]


class ActionErrorOut(BaseModel):
    status: int
    code: str
    message: str


class ActionResultOut(BaseModel):
    index: int
    action: str
    status: Literal["done", "failed", "skipped"]
    data: dict
    error: Optional[ActionErrorOut] = None


class ExecuteOut(BaseModel):
    ok: bool
    done_count: int
    failed_index: Optional[int] = None
    results: list[ActionResultOut]


class TranscribeOut(BaseModel):
    text: str
