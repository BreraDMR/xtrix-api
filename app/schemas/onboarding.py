"""Wire contract for the first-run checklist.

The server derives completion from real business data; this module only fixes the
shape so the web app can render role-aware steps without deciding who may do what.
Labels and hints stay on the client and are addressed by these stable keys.
"""
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

from app.schemas.base import InputModel
from app.schemas.errors import invalid

OnboardingPersona = Literal["owner", "solo", "staff"]
OnboardingStepKey = Literal["business", "service", "staff", "schedule", "booking"]
OnboardingHintKey = Literal["clients", "services", "calendar"]
ONBOARDING_HINT_KEYS: tuple[str, ...] = ("clients", "services", "calendar")


class OnboardingStep(BaseModel):
    key: OnboardingStepKey
    completed: bool
    available: bool = Field(
        True,
        description="False while the screen behind the step is not released yet",
    )
    required: bool = Field(
        True,
        description="Only required steps count towards progress",
    )


class OnboardingStateOut(BaseModel):
    persona: OnboardingPersona
    steps: list[OnboardingStep]
    completed_steps: int = Field(ge=0)
    total_steps: int = Field(ge=0)
    progress_percent: int = Field(ge=0, le=100)
    next_step: Optional[OnboardingStepKey] = None
    onboarding_version: int
    tour_version: int
    tour_completed: bool
    tour_dismissed: bool
    welcome_dismissed: bool
    checklist_dismissed: bool
    dismissed_hints: list[OnboardingHintKey]


class OnboardingUpdate(InputModel):
    tour_completed: Optional[bool] = None
    tour_dismissed: Optional[bool] = None
    welcome_dismissed: Optional[bool] = None
    checklist_dismissed: Optional[bool] = None
    dismissed_hints: Optional[list[OnboardingHintKey]] = None

    @field_validator("dismissed_hints")
    @classmethod
    def unique_hints(cls, value):
        return None if value is None else list(dict.fromkeys(value))

    @model_validator(mode="after")
    def tour_state_is_one_thing(self):
        if self.tour_completed and self.tour_dismissed:
            raise invalid("tour_state_conflict", "Tour cannot be both completed and dismissed")
        return self
