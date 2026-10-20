"""Server-side validation of guest answers against a published field snapshot."""
import hashlib
import json
from typing import Any, Optional

from pydantic import TypeAdapter, ValidationError

from app.schemas.questionnaire import (
    QuestionnaireBooleanField,
    QuestionnaireFieldList,
    QuestionnaireLongTextField,
    QuestionnaireMultiChoiceField,
    QuestionnaireSingleChoiceField,
    QuestionnaireTextField,
)

_FIELDS = TypeAdapter(QuestionnaireFieldList)
MAX_TOTAL_ANSWER_CHARS = 20_000


class AnswersInvalid(Exception):
    def __init__(self, fields: dict[str, str]):
        super().__init__("invalid questionnaire answers")
        self.fields = fields


def _plain_problem(value: str, multiline: bool) -> Optional[str]:
    for char in value:
        code = ord(char)
        if code == 127 or (code < 32 and not (multiline and char in "\n\t")):
            return "invalid_text"
    return None


def _clean_text(value: str, max_length: int, multiline: bool) -> tuple[Optional[str], Optional[str]]:
    cleaned = value.strip()
    if not cleaned:
        return None, "empty"
    if len(cleaned) > max_length:
        return None, "too_long"
    problem = _plain_problem(cleaned, multiline)
    if problem:
        return None, problem
    return cleaned, None


def _clean_multi(field: QuestionnaireMultiChoiceField, value: Any) -> tuple[Optional[list[str]], Optional[str]]:
    if not isinstance(value, list):
        return None, "expected_list"
    items: list[str] = []
    for item in value:
        if not isinstance(item, str):
            return None, "expected_text"
        item = item.strip()
        if not item:
            return None, "invalid_option"
        items.append(item)
    if not items:
        return None, "empty"
    if len(items) != len(set(items)):
        return None, "duplicate_option"
    if field.max_selected is not None and len(items) > field.max_selected:
        return None, "too_many_selected"
    allowed = {option.value for option in field.options}
    if any(item not in allowed for item in items):
        return None, "invalid_option"
    return items, None


def _answer_size(value: Any) -> int:
    if isinstance(value, str):
        return len(value)
    if isinstance(value, list):
        return sum(len(item) for item in value if isinstance(item, str))
    return 0


def normalize_answers(version_fields: list[dict], raw: dict[str, Any]) -> dict[str, Any]:
    try:
        fields = _FIELDS.validate_python(version_fields)
    except ValidationError:
        raise AnswersInvalid({"answers": "invalid_version"})

    by_key = {field.key: field for field in fields}
    errors: dict[str, str] = {key: "unknown_field" for key in raw if key not in by_key}
    normalized: dict[str, Any] = {}

    for field in fields:
        value = raw.get(field.key)
        present = field.key in raw and value is not None

        if isinstance(field, (QuestionnaireTextField, QuestionnaireLongTextField)):
            if not present:
                if field.required:
                    errors[field.key] = "required"
                continue
            if not isinstance(value, str):
                errors[field.key] = "expected_text"
                continue
            cleaned, problem = _clean_text(
                value, field.max_length, isinstance(field, QuestionnaireLongTextField)
            )
            if problem == "empty":
                if field.required:
                    errors[field.key] = "required"
            elif problem:
                errors[field.key] = problem
            else:
                normalized[field.key] = cleaned
            continue

        if isinstance(field, QuestionnaireMultiChoiceField):
            if not present:
                if field.required:
                    errors[field.key] = "required"
                continue
            items, problem = _clean_multi(field, value)
            if problem == "empty":
                if field.required:
                    errors[field.key] = "required"
            elif problem:
                errors[field.key] = problem
            else:
                normalized[field.key] = items
            continue

        if isinstance(field, QuestionnaireSingleChoiceField):
            if not present:
                if field.required:
                    errors[field.key] = "required"
                continue
            if not isinstance(value, str):
                errors[field.key] = "expected_text"
                continue
            choice = value.strip()
            if not choice:
                if field.required:
                    errors[field.key] = "required"
            elif choice not in {option.value for option in field.options}:
                errors[field.key] = "invalid_option"
            else:
                normalized[field.key] = choice
            continue

        if isinstance(field, QuestionnaireBooleanField):
            if not present:
                if field.required:
                    errors[field.key] = "required"
                continue
            if not isinstance(value, bool):
                errors[field.key] = "expected_boolean"
                continue
            normalized[field.key] = value

    if errors:
        raise AnswersInvalid(errors)
    if sum(_answer_size(value) for value in normalized.values()) > MAX_TOTAL_ANSWER_CHARS:
        raise AnswersInvalid({"answers": "too_large"})
    return normalized


def answers_hash(answers: dict[str, Any]) -> str:
    canonical = json.dumps(answers, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
