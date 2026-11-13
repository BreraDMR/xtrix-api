"""What make_plan fixes in the model's plan before anybody sees it (duty plan item 8).

The executor still refuses foreign ids on its own (test_ai_assistant), this is about the person not
being shown a step that can never work, a time an hour off after the clock change, or a booking in
the past - they get a concrete question instead.
"""
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app.ai import assistant
from tests import test_ai_assistant as base
from tests.test_ai_assistant import Model, fresh, setup

ab = base.ab

# Monday 19.10.2026 09:15 in Prague (still summer time, +02:00), as naive UTC
NOW = datetime(2026, 10, 19, 7, 15)
WRITES = ("create_client", "update_client", "create_visit", "reschedule_visit", "cancel_visit", "add_note")


def plan_for(db, who, *actions, summary="ok", questions=(), locale="en", now=NOW):
    kit = setup(Model({"summary": summary, "questions": list(questions), "actions": list(actions)}))
    return assistant.make_plan(db, fresh(db, who["owner"]), "request", locale=locale,
                               tz="Europe/Prague", now=now, **kit).plan


def test_foreign_ids_are_dropped_before_the_plan_is_shown(db, ab):
    a, b = ab
    plan = plan_for(db, b, {"action": "cancel_visit", "visit_id": a["visit"]["id"]},
                    {"action": "update_client", "client_id": a["client"]["id"], "full_name": "x"},
                    {"action": "add_note", "visit_id": 2_000_000_000, "text": "hi"},
                    summary="I'll cancel the visit and rename the client")
    assert plan.actions == []
    assert len(plan.questions) == 1 and "Which client or visit" in plan.questions[0]
    assert "outside your salon" in plan.summary and "cancel" not in plan.summary


def test_own_steps_stay_and_steps_built_on_a_dropped_one_go_too(db, ab):
    a, b = ab
    plan = plan_for(db, b,
                    {"action": "find_free_slot", "ref": "s", "specialist_id": a["owner"].id},
                    {"action": "create_visit", "client_id": b["client"]["id"], "slot_ref": "s"},
                    {"action": "add_note", "visit_id": b["visit"]["id"], "text": "bring towels"},
                    summary="Note and booking", locale="ru")
    assert [x.action for x in plan.actions] == ["add_note"]
    assert plan.actions[0].visit_id == b["visit"]["id"]
    assert plan.questions and "Уточните" in plan.questions[0]
    assert plan.summary.endswith("Note and booking")


def test_foreign_service_or_specialist_in_a_booking_is_dropped(db, ab):
    a, b = ab
    plan = plan_for(db, b, {"action": "create_visit", "client_id": b["client"]["id"],
                            "service_id": a["service"]["id"], "start": "2026-10-20T10:00:00+02:00"})
    assert plan.actions == []


@pytest.mark.parametrize("given, fixed", [
    ("2026-10-27T10:00:00+02:00", "2026-10-27T10:00:00+01:00"),   # summer offset on a winter date
    ("2026-10-20T10:00:00+01:00", "2026-10-20T10:00:00+02:00"),   # and the other way round
    ("2026-10-27T10:00:00+01:00", "2026-10-27T10:00:00+01:00"),   # already right
    ("2026-10-27T09:00:00+00:00", "2026-10-27T09:00:00+00:00"),   # plain UTC is an instant, kept
])
def test_the_salon_wall_clock_wins_over_a_wrong_dst_offset(db, ab, given, fixed):
    _, b = ab
    plan = plan_for(db, b, {"action": "create_visit", "client_id": b["client"]["id"], "start": given},
                    {"action": "reschedule_visit", "visit_id": b["visit"]["id"], "start": given},
                    {"action": "find_free_slot", "specialist_id": b["owner"].id, "not_before": given})
    want = datetime.fromisoformat(fixed)
    assert [x.action for x in plan.actions] == ["create_visit", "reschedule_visit", "find_free_slot"]
    for got in (plan.actions[0].start, plan.actions[1].start, plan.actions[2].not_before):
        assert got == want and got.utcoffset() == want.utcoffset()
    assert plan.questions == []


@pytest.mark.parametrize("locale, word", [("en", "already passed"), ("ru", "уже прошло"), ("cz", "už uplynul")])
def test_a_booking_in_the_past_becomes_a_question(db, ab, locale, word):
    _, b = ab
    plan = plan_for(db, b, {"action": "create_visit", "client_id": b["client"]["id"],
                            "start": "2026-10-19T08:00:00+02:00"},
                    {"action": "reschedule_visit", "visit_id": b["visit"]["id"],
                     "start": "2026-10-18T15:00:00+02:00"}, locale=locale)
    assert plan.actions == []
    assert len(plan.questions) == 1 and word in plan.questions[0]


def test_a_booking_that_just_started_is_still_fine(db, ab):
    _, b = ab
    # 09:00 when it's 09:15 - the client is sitting in the chair, that's not a mistake
    plan = plan_for(db, b, {"action": "create_visit", "client_id": b["client"]["id"],
                            "start": "2026-10-19T09:00:00+02:00"})
    assert [x.action for x in plan.actions] == ["create_visit"] and plan.questions == []


def test_questions_stay_within_the_limit(db, ab):
    a, b = ab
    asked = [f"q{i}" for i in range(5)]
    plan = plan_for(db, b, {"action": "cancel_visit", "visit_id": a["visit"]["id"]}, questions=asked)
    assert plan.questions == asked and plan.actions == []


@pytest.mark.parametrize("summary, asked", [
    ("Prosím upřesněte, který záznam chcete přeložit – máte datum nebo ID?", True),
    ("You're welcome!", False),
])
def test_a_question_written_only_in_the_summary_becomes_a_question(db, ab, summary, asked):
    # slot8 13.11: the model asked in the summary and left questions empty, so the UI had nothing to answer
    _, b = ab
    plan = plan_for(db, b, summary=summary, locale="cz")
    assert plan.questions == ([summary] if asked else [])
    assert plan.summary == summary


def test_the_prompt_does_not_book_through_a_free_slot_when_only_the_time_is_missing():
    from app.ai.prompt import system_prompt
    text = system_prompt()
    assert "only the time is missing" in text and "matching clients or services)" in text


# --- find_client that can't work as asked (dialog evals 13.11) ---

def test_find_client_nobody_matches_becomes_a_question(db, ab):
    _, b = ab
    plan = plan_for(db, b, {"action": "find_client", "ref": "c", "name": "Zinaida Pupkina"},
                    {"action": "create_visit", "client_ref": "c", "start": "2026-10-20T10:00:00+02:00"},
                    summary="I'll book Zinaida Pupkina", locale="ru")
    assert plan.actions == []
    assert plan.questions and "Zinaida Pupkina" in plan.questions[0]
    assert "I'll book" not in plan.summary


def test_find_client_with_several_matches_asks_which_one(db, ab, client):
    _, b = ab
    r = client.post("/clients/", json={"full_name": "Beta Klientka Druhá"}, headers=b["headers"])
    assert r.status_code == 200, r.text
    second = r.json()
    plan = plan_for(db, b, {"action": "find_client", "ref": "c", "name": "Klientka"},
                    {"action": "create_visit", "client_ref": "c", "start": "2026-10-20T10:00:00+02:00"},
                    summary="Booking", locale="en")
    assert plan.actions == []
    question = plan.questions[0]
    assert b["client"]["full_name"] in question and second["full_name"] in question
    assert "Which one" in question


def test_find_client_that_resolves_stays(db, ab):
    _, b = ab
    plan = plan_for(db, b, {"action": "find_client", "ref": "c", "name": b["client"]["full_name"]},
                    {"action": "create_visit", "client_ref": "c", "start": "2026-10-20T10:00:00+02:00"},
                    summary="Booking")
    assert [x.action for x in plan.actions] == ["find_client", "create_visit"]


@pytest.mark.parametrize("said, shown", [
    ("Service «Haircut» is only done by Olga Belova (ID 747).", "Service «Haircut» is only done by Olga Belova."),
    ("Client ID: 12 has a visit at 10:00", "Client has a visit at 10:00"),
    ("Booking for client_id=12 at 10:00", "Booking for client at 10:00"),
    ("Room 12 at 10:00, phone +420 777 100 001", "Room 12 at 10:00, phone +420 777 100 001"),
])
def test_internal_ids_never_reach_the_person(db, ab, said, shown):
    _, b = ab
    plan = plan_for(db, b, summary=said, questions=[said])
    assert plan.summary == shown and plan.questions == [shown]


# --- a time the master already has taken (dialog evals 13.11) ---

def _taken(b):
    return datetime.fromisoformat(b["visit"]["date_start"].replace("Z", "")).isoformat() + "Z"


@pytest.mark.parametrize("locale, word", [("ru", "занят"), ("en", "already booked"), ("cz", "obsazen")])
def test_a_booking_on_a_taken_time_becomes_a_question(db, ab, locale, word):
    _, b = ab
    plan = plan_for(db, b, {"action": "create_visit", "client_id": b["client"]["id"],
                            "specialist_id": b["owner"].id, "start": _taken(b)},
                    summary="Booked it", locale=locale)
    assert plan.actions == []
    assert plan.questions and word in plan.questions[0]
    assert "Booked it" not in plan.summary


def test_a_free_time_and_moving_a_visit_onto_itself_stay(db, ab):
    from datetime import timedelta
    _, b = ab
    later = (datetime.fromisoformat(_taken(b).replace("Z", "")) + timedelta(hours=3)).isoformat() + "Z"
    plan = plan_for(db, b, {"action": "create_visit", "client_id": b["client"]["id"],
                            "specialist_id": b["owner"].id, "start": later},
                    {"action": "reschedule_visit", "visit_id": b["visit"]["id"], "start": _taken(b)})
    assert [x.action for x in plan.actions] == ["create_visit", "reschedule_visit"]


def test_moving_onto_a_taken_time_becomes_a_question(db, ab, client):
    from datetime import timedelta
    _, b = ab
    start = datetime.fromisoformat(_taken(b).replace("Z", "")) + timedelta(hours=4)
    other = client.post("/visits/", headers=b["headers"], json={
        "client_id": b["client"]["id"], "specialist_id": b["owner"].id, "service_id": b["service"]["id"],
        "date_start": start.isoformat(), "date_end": (start + timedelta(hours=1)).isoformat()})
    assert other.status_code == 200, other.text
    plan = plan_for(db, b, {"action": "reschedule_visit", "visit_id": b["visit"]["id"],
                            "start": start.isoformat() + "Z"}, locale="ru")
    assert plan.actions == [] and "занят" in plan.questions[0]


def test_a_taken_time_offers_the_next_free_ones(db, ab, client):
    _, b = ab
    for weekday in range(7):
        r = client.post("/schedules/work", headers=b["headers"], json={
            "weekday": weekday, "start_time": "08:00", "end_time": "20:00", "specialist_id": b["owner"].id})
        assert r.status_code == 200, r.text
    plan = plan_for(db, b, {"action": "create_visit", "client_id": b["client"]["id"],
                            "specialist_id": b["owner"].id, "service_id": b["service"]["id"],
                            "start": _taken(b)}, locale="ru")
    question = plan.questions[0]
    assert "Свободно:" in question
    # the taken hour ends at 11:00 UTC, so the first offer is the wall-clock hour after it, not the taken one
    taken_local = datetime.fromisoformat(_taken(b).replace("Z", "+00:00")).astimezone(ZoneInfo("Europe/Prague"))
    assert taken_local.strftime("%H:%M") + "," not in question.split("Свободно:")[1]
