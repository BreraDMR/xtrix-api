"""The assistant's name and grammatical gender for a business (Damir, 18.10.2026).

The name goes into the model prompt only as data, never as part of the instructions.
"""
from dataclasses import dataclass
from typing import Optional

from app.api.core.i18n import normalize_locale

DEFAULT_NAMES = {"ru": "Сашенька", "cz": "Saška", "en": "Sasha"}
GENDERS = ("female", "male", "neuter")
DEFAULT_GENDER = "female"
MAX_NAME = 30


@dataclass(frozen=True)
class AssistantPersona:
    name: str
    gender: str
    is_default: bool


def effective_assistant(business, language: Optional[str]) -> AssistantPersona:
    own = (getattr(business, "assistant_name", None) or "").strip()
    gender = getattr(business, "assistant_gender", None) or DEFAULT_GENDER
    if gender not in GENDERS:
        gender = DEFAULT_GENDER
    if own:
        return AssistantPersona(own, gender, False)
    return AssistantPersona(DEFAULT_NAMES[normalize_locale(language)], gender, True)
