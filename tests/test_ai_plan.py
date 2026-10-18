"""The JSON plan the model must return: strict, small, and with nothing medical in it."""
import json

import pytest
from pydantic import ValidationError

from app.ai.plan import MAX_ACTIONS, AssistantPlan


def plan(*actions, **extra):
    return {"summary": "ok", "questions": [], "actions": list(actions), **extra}


def check(data):
    return AssistantPlan.model_validate_json(json.dumps(data))


def test_full_booking_plan():
    p = check(plan(
        {"action": "create_client", "ref": "new", "full_name": "Ivan Ivanov", "birth_date": "2006-10-19"},
        {"action": "find_free_slot", "ref": "slot", "specialist_id": 3, "service_id": 7,
         "not_before": "2026-10-22T00:00:00+02:00"},
        {"action": "create_visit", "client_ref": "new", "slot_ref": "slot", "service_id": 7},
    ))
    assert [a.action for a in p.actions] == ["create_client", "find_free_slot", "create_visit"]


def test_every_v1_action_parses():
    p = check(plan(
        {"action": "find_client", "ref": "c", "name": "Novak"},
        {"action": "update_client", "client_ref": "c", "contact_phone": "+420777000111"},
        {"action": "create_visit", "client_id": 5, "start": "2026-10-22T10:00:00+02:00"},
        {"action": "reschedule_visit", "visit_id": 9, "start": "2026-10-23T10:00:00Z"},
        {"action": "cancel_visit", "visit_id": 9},
        {"action": "add_note", "visit_id": 9, "text": "bring towel"},
    ))
    assert len(p.actions) == 6


@pytest.mark.parametrize("field", ["allergies", "chronic_diseases", "pregnancy", "doctor_notes", "medical_profile"])
def test_medical_fields_are_refused(field):
    with pytest.raises(ValidationError):
        check(plan({"action": "update_client", "client_id": 1, field: "dimedrol"}))
    with pytest.raises(ValidationError):
        check(plan({"action": "create_client", "full_name": "X", field: "dimedrol"}))


@pytest.mark.parametrize("bad", [
    {"action": "drop_database"},
    {"action": "cancel_visit", "visit_id": "9"},  # strings are not ids
    {"action": "cancel_visit", "visit_id": True},
    {"action": "cancel_visit", "visit_id": 0},
    {"action": "cancel_visit"},
    {"action": "create_visit", "client_id": 1, "start": "2026-10-22T10:00:00"},  # no offset - ambiguous
    {"action": "create_visit", "client_id": 1},  # no time at all
    {"action": "create_visit", "client_id": 1, "client_ref": "x", "start": "2026-10-22T10:00:00Z"},
    {"action": "update_client", "client_id": 1},  # nothing to change
    {"action": "find_client"},
    {"action": "add_note", "visit_id": 1, "text": ""},
    {"action": "add_note", "visit_id": 1, "text": "x" * 1001},
    {"action": "create_client", "full_name": "x" * 256},
    {"action": "find_free_slot", "duration_minutes": 0},
    {"action": "create_client", "full_name": "A", "role": "admin"},
])
def test_bad_actions_are_rejected(bad):
    with pytest.raises(ValidationError):
        check(plan(bad))


def test_limits_on_size():
    with pytest.raises(ValidationError):
        check(plan(*[{"action": "cancel_visit", "visit_id": i + 1} for i in range(MAX_ACTIONS + 1)]))
    with pytest.raises(ValidationError):
        check(plan(summary="x" * 501))
    with pytest.raises(ValidationError):
        check(plan(questions=["?"] * 6))
    with pytest.raises(ValidationError):
        check(plan(extra_field=1))


def test_refs_must_be_defined_earlier_and_be_the_right_kind():
    with pytest.raises(ValidationError):
        check(plan({"action": "create_visit", "client_ref": "ghost", "start": "2026-10-22T10:00:00Z"}))
    with pytest.raises(ValidationError):
        # used before it's made
        check(plan(
            {"action": "create_visit", "client_ref": "c", "start": "2026-10-22T10:00:00Z"},
            {"action": "create_client", "ref": "c", "full_name": "A"},
        ))
    with pytest.raises(ValidationError):
        # a slot is not a client
        check(plan(
            {"action": "find_free_slot", "ref": "s"},
            {"action": "create_visit", "client_ref": "s", "slot_ref": "s"},
        ))
    with pytest.raises(ValidationError):
        check(plan(
            {"action": "create_client", "ref": "c", "full_name": "A"},
            {"action": "create_client", "ref": "c", "full_name": "B"},
        ))


def test_empty_plan_with_questions_is_fine():
    p = check(plan(questions=["Which day?"]))
    assert p.actions == [] and p.questions == ["Which day?"]


def test_json_schema_is_exportable_for_the_prompt():
    schema = AssistantPlan.model_json_schema()
    text = json.dumps(schema)
    assert "create_visit" in text and "allergies" not in text
