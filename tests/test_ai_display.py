"""The read-only preview next to each planned action: own data resolves, anything else is empty."""
import json
from datetime import datetime, timedelta, timezone

import pytest

from app.ai.display import describe_plan
from app.ai.plan import AssistantPlan
from app.api.crud import crud_user
from app.db.models.medical_profile import MedicalProfile
from tests.tenant_world import build_tenant, configure_owner_schedule

EMPTY = {"client_name": None, "service_name": None, "specialist_name": None, "start_time": None,
         "end_time": None, "note": None}


@pytest.fixture
def ab(client, db, make_user, headers_for):
    a = build_tenant(client, make_user, headers_for, "Alpha", "+42070011")
    b = build_tenant(client, make_user, headers_for, "Beta", "+42070022")
    for tenant in (a, b):
        configure_owner_schedule(db, tenant)
    return a, b


def fresh(db, user):
    db.expire_all()
    return crud_user.get_active_user(db, user.id)


def plan(*actions):
    return AssistantPlan.model_validate_json(json.dumps({"summary": "s", "questions": [], "actions": list(actions)}))


def show(db, user, *actions):
    return [d.to_dict() for d in describe_plan(db, user, plan(*actions), tz="Europe/Prague")]


def iso(dt):
    return dt.replace(tzinfo=timezone.utc).isoformat()


def test_own_visit_resolves_to_names_and_times(db, ab):
    _, b = ab
    owner = fresh(db, b["owner"])
    [d] = show(db, owner, {"action": "cancel_visit", "visit_id": b["visit"]["id"]})
    assert (d["client_name"], d["service_name"], d["specialist_name"]) == ("Beta Klientka", "Beta Massage", "Beta Owner")
    assert d["start_time"] == datetime.fromisoformat(b["visit"]["date_start"].rstrip("Z"))
    assert d["end_time"] - d["start_time"] == timedelta(hours=1)


def test_another_business_ids_resolve_to_nothing(db, ab):
    a, b = ab
    owner = fresh(db, b["owner"])
    shown = show(db, owner,
                 {"action": "cancel_visit", "visit_id": a["visit"]["id"]},
                 {"action": "add_note", "visit_id": a["visit"]["id"], "text": "hi"},
                 {"action": "reschedule_visit", "visit_id": a["visit"]["id"], "start": "2026-11-02T10:00:00+01:00"},
                 {"action": "update_client", "client_id": a["client"]["id"], "full_name": "x"},
                 {"action": "create_visit", "client_id": a["client"]["id"], "service_id": a["service"]["id"],
                  "specialist_id": a["owner"].id, "start": "2026-11-02T10:00:00+01:00"})
    assert shown[:4] == [EMPTY] * 4
    last = shown[4]
    assert (last["client_name"], last["service_name"], last["specialist_name"]) == (None, None, None)
    assert "Alpha" not in json.dumps(shown, default=str)


def test_unknown_ids_resolve_to_nothing(db, ab):
    _, b = ab
    owner = fresh(db, b["owner"])
    assert show(db, owner, {"action": "cancel_visit", "visit_id": 999999},
                {"action": "update_client", "client_id": 999999, "full_name": "x"}) == [EMPTY, EMPTY]


def test_never_contact_birth_date_or_medical(db, ab):
    _, b = ab
    db.add(MedicalProfile(client_id=b["client"]["id"], allergies="dimedrol", chronic_diseases="asthma"))
    db.commit()
    owner = fresh(db, b["owner"])
    shown = show(db, owner,
                 {"action": "create_client", "ref": "n", "full_name": "Ivan Ivanov", "birth_date": "2006-10-19",
                  "contact_phone": "+420777000111", "contact_email": "ivan@example.com"},
                 {"action": "find_client", "ref": "c", "name": "Beta Klientka"},
                 {"action": "create_visit", "client_ref": "c", "start": "2026-11-02T10:00:00+01:00"},
                 {"action": "cancel_visit", "visit_id": b["visit"]["id"]})
    text = json.dumps(shown, default=str)
    for leak in ("777000111", "ivan@example.com", "2006-10-19", b["client"]["contact_phone"],
                 b["client"]["contact_email"], "dimedrol", "asthma"):
        assert leak not in text
    assert all(set(d) == set(EMPTY) for d in shown)
    assert shown[0]["client_name"] == "Ivan Ivanov"
    assert shown[2]["client_name"] == "Beta Klientka"  # through the ref


def test_new_visit_from_a_free_slot_shows_the_time_found(db, ab):
    _, b = ab
    owner = fresh(db, b["owner"])
    find, book = show(db, owner,
                      {"action": "find_free_slot", "ref": "s", "service_id": b["service"]["id"]},
                      {"action": "create_visit", "client_id": b["client"]["id"], "slot_ref": "s",
                       "note": "first time"})
    assert find["start_time"] is not None and find["end_time"] - find["start_time"] == timedelta(minutes=60)
    assert (book["start_time"], book["end_time"]) == (find["start_time"], find["end_time"])
    assert (book["client_name"], book["service_name"], book["specialist_name"], book["note"]) == (
        "Beta Klientka", "Beta Massage", "Beta Owner", "first time")


def test_reschedule_keeps_the_visit_length(db, ab):
    _, b = ab
    owner = fresh(db, b["owner"])
    [d] = show(db, owner, {"action": "reschedule_visit", "visit_id": b["visit"]["id"],
                           "start": "2026-11-02T10:00:00+01:00"})
    assert d["start_time"] == datetime(2026, 11, 2, 9, 0)
    assert d["end_time"] == datetime(2026, 11, 2, 10, 0)
    assert d["client_name"] == "Beta Klientka"


def test_a_broken_lookup_does_not_break_the_preview(db, ab, monkeypatch):
    from app.ai import display

    _, b = ab
    owner = fresh(db, b["owner"])

    def boom(self, a):
        raise RuntimeError("bug")

    monkeypatch.setattr(display._Describer, "cancel_visit", boom)
    shown = show(db, owner, {"action": "cancel_visit", "visit_id": b["visit"]["id"]},
                 {"action": "update_client", "client_id": b["client"]["id"], "full_name": "x"})
    assert shown[0] == EMPTY and shown[1]["client_name"] == "Beta Klientka"
