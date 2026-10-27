from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from app.api.core import booking_manage_policy as legacy
from app.api.core.visitor_booking_policy import BookingManageFacts, booking_manage_eligibility

NOW = datetime(2026, 10, 24, 12)


def facts(status="planned", start=NOW + timedelta(days=2), writable=True, catalog=True):
    return BookingManageFacts(status=status, start=start, business_write_available=writable,
                              reschedule_catalog_available=catalog)


@pytest.mark.parametrize("status", ["planned", "awaiting_payment", "cancelled", "completed",
                                   "in_progress", "new_status", "", None])
@pytest.mark.parametrize("offset", [timedelta(days=-1), timedelta(), timedelta(microseconds=1),
                                    timedelta(hours=24, microseconds=-1), timedelta(hours=24),
                                    timedelta(hours=24, microseconds=1)])
def test_existing_status_time_rules_are_reused(status, offset):
    start = NOW + offset
    result = booking_manage_eligibility(facts(status=status, start=start), now=NOW)
    visit = SimpleNamespace(status=status, date_start=start)
    assert result.confirm.reason == legacy.confirmation_block_reason(visit, NOW)
    assert result.cancel.reason == legacy.cancellation_block_reason(visit, NOW)
    assert result.reschedule.reason == legacy.reschedule_block_reason(visit, NOW)
    for decision in (result.confirm, result.cancel, result.reschedule):
        assert decision.allowed is (decision.reason is None)


def test_exact_24_hours_in_utc_across_prague_fall_dst():
    # Local wall clocks differ by 23 hours, but stored instants differ by 24.
    local_now = datetime(2026, 10, 24, 14, tzinfo=ZoneInfo("Europe/Prague"))
    local_start = datetime(2026, 10, 25, 13, tzinfo=ZoneInfo("Europe/Prague"))
    result = booking_manage_eligibility(facts(start=local_start), now=local_now)
    assert result.cancel.allowed and result.reschedule.allowed
    late = booking_manage_eligibility(facts(start=local_start), now=local_now + timedelta(microseconds=1))
    assert late.cancel.reason == "booking_cancel_too_late"
    assert late.reschedule.reason == "booking_reschedule_too_late"


@pytest.mark.parametrize("available", [False, None])
def test_business_unavailable_disables_every_write_with_public_reason(available):
    result = booking_manage_eligibility(facts(writable=available), now=NOW)
    assert {d.reason for d in (result.confirm, result.cancel, result.reschedule)} == {"visitor_actions_unavailable"}


@pytest.mark.parametrize("available", [False, None])
def test_missing_current_catalog_only_disables_reschedule(available):
    result = booking_manage_eligibility(facts(catalog=available), now=NOW)
    assert result.confirm.allowed and result.cancel.allowed
    assert result.reschedule.reason == "booking_reschedule_unavailable"


@pytest.mark.parametrize("status", ["planned", "awaiting_payment", None])
def test_null_start_is_unknown_never_an_exception_or_invented_instant(status):
    result = booking_manage_eligibility(facts(status=status, start=None), now=NOW)
    assert result.confirm.reason == "booking_confirm_unavailable"
    assert result.cancel.reason == "booking_cancel_unavailable"
    assert result.reschedule.reason == "booking_reschedule_unavailable"


def test_existing_booking_management_has_no_new_booking_enabled_input():
    # Disabling a public slug prevents new bookings; this policy has no slug input.
    assert set(BookingManageFacts.__dataclass_fields__) == {
        "status", "start", "business_write_available", "reschedule_catalog_available"}
    result = booking_manage_eligibility(facts(), now=NOW)
    assert result.confirm.allowed and result.cancel.allowed and result.reschedule.allowed


def test_adapter_calls_existing_policies_with_the_same_normalized_instant(monkeypatch):
    calls = []
    for action, name in [("confirm", "confirmation"), ("cancel", "cancellation"), ("reschedule", "reschedule")]:
        def spy(visit, now, action=action):
            calls.append((action, visit.date_start, now))
            return f"legacy_{action}"
        monkeypatch.setattr(legacy, name + "_block_reason", spy)
    result = booking_manage_eligibility(facts(start=NOW.replace(tzinfo=timezone.utc)), now=NOW)
    assert calls == [(a, NOW, NOW) for a in ("confirm", "cancel", "reschedule")]
    assert result.confirm.reason == "legacy_confirm"
    assert result.cancel.reason == "legacy_cancel"
    assert result.reschedule.reason == "legacy_reschedule"


def test_facts_and_output_are_immutable():
    input_facts = facts()
    output = booking_manage_eligibility(input_facts, now=NOW)
    with pytest.raises(FrozenInstanceError):
        input_facts.status = "cancelled"
    with pytest.raises(FrozenInstanceError):
        output.confirm.reason = "other"


def test_caller_must_supply_one_clock_instant():
    with pytest.raises(TypeError):
        booking_manage_eligibility(facts())


@pytest.mark.parametrize("status,expected", [
    ("planned", (None, None, None)),
    ("awaiting_payment", ("booking_confirm_unavailable", None, "booking_reschedule_unavailable")),
    ("completed", ("booking_confirm_completed", "booking_cancel_completed", "booking_reschedule_completed")),
    ("in_progress", ("booking_confirm_in_progress", "booking_cancel_in_progress", "booking_reschedule_in_progress")),
    ("cancelled", ("booking_confirm_cancelled", "booking_cancel_already", "booking_reschedule_cancelled")),
    (None, ("booking_confirm_unavailable", "booking_cancel_unavailable", "booking_reschedule_unavailable")),
    ("unknown", ("booking_confirm_unavailable", "booking_cancel_unavailable", "booking_reschedule_unavailable")),
])
def test_approved_status_decisions(status, expected):
    result = booking_manage_eligibility(facts(status=status), now=NOW)
    assert (result.confirm.reason, result.cancel.reason, result.reschedule.reason) == expected


def test_past_unpaid_hold_can_be_released_but_planned_cannot_be_confirmed_or_moved():
    hold = booking_manage_eligibility(facts(status="awaiting_payment", start=NOW - timedelta(days=3)), now=NOW)
    assert hold.cancel.allowed
    planned = booking_manage_eligibility(facts(start=NOW), now=NOW)
    assert planned.confirm.reason == "booking_confirm_started"
    assert planned.cancel.reason == "booking_cancel_too_late"
    assert planned.reschedule.reason == "booking_reschedule_started"
