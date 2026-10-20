"""Wire contract for the first-run checklist.

The server derives completion from real business data; this module only fixes the
shape so the web app can render role-aware steps without deciding who may do what.
Labels and hints stay on the client and are addressed by these stable keys.
"""
from typing import Literal, Optional

from pydantic import BaseModel, Field

OnboardingPersona = Literal["owner", "solo", "staff"]
OnboardingStepKey = Literal["business", "service", "staff", "schedule", "booking"]
OnboardingHintKey = Literal["clients", "services", "calendar"]


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
