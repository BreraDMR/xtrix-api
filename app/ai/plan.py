"""The plan the model returns (А2). We run it ourselves after the user confirms - the model never
calls anything. Everything is strict: unknown fields, unknown actions and loose types are rejected,
so a medical field or a made-up action can't sneak through however the model is talked into it.
"""
from datetime import date
from typing import Annotated, Literal, Optional, Union

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints, model_validator

MAX_ACTIONS = 10
MAX_QUESTIONS = 5

Id = Annotated[int, Field(ge=1, le=2_147_483_647)]
Ref = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{0,19}$",
                           description="Short local name for this action's result, used by later actions")]
Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)]
Phone = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=50)]
Email = Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=255)]
Note = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)]
Gender = Literal["male", "female", "other"]
Language = Literal["en", "ru", "cz"]


class _Action(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


def _one_of(obj, *names):
    given = [n for n in names if getattr(obj, n) is not None]
    if len(given) != 1:
        raise ValueError(f"give exactly one of: {', '.join(names)}")


class FindClient(_Action):
    """Find one of the user's own clients by name, phone or e-mail."""
    action: Literal["find_client"]
    ref: Optional[Ref] = None
    name: Optional[Name] = None
    phone: Optional[Phone] = None
    email: Optional[Email] = None

    @model_validator(mode="after")
    def _something(self):
        if not (self.name or self.phone or self.email):
            raise ValueError("give a name, phone or email")
        return self


class CreateClient(_Action):
    """New client card. No medical data here, ever."""
    action: Literal["create_client"]
    ref: Optional[Ref] = None
    full_name: Name
    birth_date: Optional[date] = None
    gender: Optional[Gender] = None
    contact_phone: Optional[Phone] = None
    contact_email: Optional[Email] = None
    language: Optional[Language] = None


class UpdateClient(_Action):
    """Change contact details of an existing client. No medical data."""
    action: Literal["update_client"]
    client_id: Optional[Id] = None
    client_ref: Optional[Ref] = None
    full_name: Optional[Name] = None
    birth_date: Optional[date] = None
    gender: Optional[Gender] = None
    contact_phone: Optional[Phone] = None
    contact_email: Optional[Email] = None
    language: Optional[Language] = None

    @model_validator(mode="after")
    def _target_and_change(self):
        _one_of(self, "client_id", "client_ref")
        if not any(getattr(self, f) is not None for f in
                   ("full_name", "birth_date", "gender", "contact_phone", "contact_email", "language")):
            raise ValueError("nothing to change")
        return self


class FindFreeSlot(_Action):
    """Earliest free time for a specialist. Our code searches; the model only says what to look for."""
    action: Literal["find_free_slot"]
    ref: Optional[Ref] = None
    specialist_id: Optional[Id] = None
    service_id: Optional[Id] = None
    duration_minutes: Optional[Annotated[int, Field(ge=1, le=24 * 60)]] = None
    not_before: Optional[AwareDatetime] = None
    days_ahead: Annotated[int, Field(ge=1, le=31)] = 7


class CreateVisit(_Action):
    action: Literal["create_visit"]
    client_id: Optional[Id] = None
    client_ref: Optional[Ref] = None
    specialist_id: Optional[Id] = None
    service_id: Optional[Id] = None
    start: Optional[AwareDatetime] = None
    slot_ref: Optional[Ref] = None
    duration_minutes: Optional[Annotated[int, Field(ge=1, le=24 * 60)]] = None
    note: Optional[Note] = None

    @model_validator(mode="after")
    def _who_and_when(self):
        _one_of(self, "client_id", "client_ref")
        _one_of(self, "start", "slot_ref")
        return self


class RescheduleVisit(_Action):
    action: Literal["reschedule_visit"]
    visit_id: Id
    start: Optional[AwareDatetime] = None
    slot_ref: Optional[Ref] = None

    @model_validator(mode="after")
    def _when(self):
        _one_of(self, "start", "slot_ref")
        return self


class CancelVisit(_Action):
    action: Literal["cancel_visit"]
    visit_id: Id


class AddNote(_Action):
    """Append a line to a visit's note."""
    action: Literal["add_note"]
    visit_id: Id
    text: Note


Action = Annotated[
    Union[FindClient, CreateClient, UpdateClient, FindFreeSlot, CreateVisit, RescheduleVisit, CancelVisit, AddNote],
    Field(discriminator="action"),
]

# which actions make which kind of thing a later action can point at
CLIENT_MAKERS = ("find_client", "create_client")
SLOT_MAKERS = ("find_free_slot",)


class AssistantPlan(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    summary: Annotated[str, StringConstraints(max_length=500)]
    questions: list[Annotated[str, StringConstraints(min_length=1, max_length=300)]] = Field(
        default_factory=list, max_length=MAX_QUESTIONS)
    actions: list[Action] = Field(default_factory=list, max_length=MAX_ACTIONS)

    @model_validator(mode="after")
    def _refs_point_backwards(self):
        made: dict[str, str] = {}
        for i, a in enumerate(self.actions):
            for field, kinds in (("client_ref", CLIENT_MAKERS), ("slot_ref", SLOT_MAKERS)):
                ref = getattr(a, field, None)
                if ref is not None and made.get(ref) not in kinds:
                    raise ValueError(f"actions[{i}].{field} '{ref}' must name an earlier "
                                     f"{' or '.join(kinds)} action")
            ref = getattr(a, "ref", None)
            if ref is not None:
                if ref in made:
                    raise ValueError(f"ref '{ref}' is used twice")
                made[ref] = a.action
        return self
