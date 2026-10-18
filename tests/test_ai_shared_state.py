"""Kept plans and resting slots live in Redis when it answers, in process memory when it doesn't."""
from datetime import datetime, timedelta, timezone

import pytest

from app.ai import assistant, shared
from app.ai.cooldown import CooldownBoard
from app.ai.plan import AssistantPlan
from tests.fake_redis import FakeRedis

PLAN = AssistantPlan.model_validate_json(
    '{"summary": "s", "questions": [], "actions": [{"action": "create_visit", "client_id": 5, '
    '"start": "2026-10-22T09:00:00+02:00", "note": "first time"}]}')


@pytest.fixture
def clock():
    now = [datetime(2026, 10, 18, 12, 0, tzinfo=timezone.utc)]
    return now


def redis_store(fake, clock):
    return assistant.SharedPlanStore(redis=lambda: fake, clock=lambda: clock[0])


# --- plan store --------------------------------------------------------------

def test_redis_store_round_trip_is_one_shot(clock):
    fake = FakeRedis(clock=lambda: clock[0])
    store = redis_store(fake, clock)
    plan_id, expires_at = store.save(user_id=7, plan=PLAN)
    assert expires_at == clock[0] + timedelta(minutes=10)
    [key] = list(fake.data)
    # the key holds the user and a hash of the id - never the id itself
    assert key.startswith("ai:plan:7:") and plan_id not in key
    assert store.peek(plan_id, user_id=7) == PLAN
    assert store.take(plan_id, user_id=7) == PLAN
    with pytest.raises(assistant.PlanNotFound):
        store.take(plan_id, user_id=7)
    assert fake.data == {}


def test_redis_store_keeps_the_plan_for_ten_minutes_only(clock):
    fake = FakeRedis(clock=lambda: clock[0])
    store = redis_store(fake, clock)
    plan_id, _ = store.save(user_id=7, plan=PLAN)
    assert fake.data[next(iter(fake.data))][1] == pytest.approx(clock[0].timestamp() + 600)
    clock[0] += timedelta(minutes=10, seconds=1)
    with pytest.raises(assistant.PlanNotFound):
        store.take(plan_id, user_id=7)


def test_someone_elses_take_does_not_burn_my_plan(clock):
    fake = FakeRedis(clock=lambda: clock[0])
    store = redis_store(fake, clock)
    plan_id, _ = store.save(user_id=7, plan=PLAN)
    with pytest.raises(assistant.PlanNotFound):
        store.take(plan_id, user_id=8)
    assert store.take(plan_id, user_id=7) == PLAN


def test_two_workers_share_plans_through_redis(clock):
    fake = FakeRedis(clock=lambda: clock[0])
    worker_a, worker_b = redis_store(fake, clock), redis_store(fake, clock)
    plan_id, _ = worker_a.save(user_id=7, plan=PLAN)
    assert worker_b.take(plan_id, user_id=7) == PLAN
    with pytest.raises(assistant.PlanNotFound):
        worker_a.take(plan_id, user_id=7)


def test_no_redis_means_process_memory(clock):
    store = assistant.SharedPlanStore(redis=lambda: None, clock=lambda: clock[0])
    plan_id, _ = store.save(user_id=7, plan=PLAN)
    assert store.take(plan_id, user_id=7) == PLAN
    with pytest.raises(assistant.PlanNotFound):
        store.take(plan_id, user_id=7)


def test_redis_dropping_out_falls_back_to_memory(clock):
    fake = FakeRedis(clock=lambda: clock[0])
    store = redis_store(fake, clock)
    fake.down = True
    plan_id, _ = store.save(user_id=7, plan=PLAN)  # no 500, kept in memory
    fake.down = False
    assert store.take(plan_id, user_id=7) == PLAN  # found in memory after Redis misses


def test_a_damaged_redis_value_is_just_not_found(clock):
    fake = FakeRedis(clock=lambda: clock[0])
    store = redis_store(fake, clock)
    plan_id, _ = store.save(user_id=7, plan=PLAN)
    key = next(iter(fake.data))
    fake.data[key] = ('{"user_id": 7, "plan": {"summary": "s", "actions": [{"action": "drop_db"}]}}', None)
    with pytest.raises(assistant.PlanNotFound):
        store.take(plan_id, user_id=7)


# --- cooldown board ----------------------------------------------------------

def test_redis_board_is_shared_between_workers(clock):
    fake = FakeRedis(clock=lambda: clock[0])
    a = shared.SharedCooldownBoard("llm", redis=lambda: fake, clock=lambda: clock[0])
    b = shared.SharedCooldownBoard("llm", redis=lambda: fake, clock=lambda: clock[0])
    a.cool("slot1", seconds=60)
    assert b.remaining("slot1") == pytest.approx(60)
    assert list(fake.data) == ["ai:cool:llm:slot1"]
    # a shorter rest doesn't cut a longer one short
    b.cool("slot1", seconds=10)
    assert a.remaining("slot1") == pytest.approx(60)
    clock[0] += timedelta(seconds=61)
    assert a.remaining("slot1") == 0 and b.remaining("slot1") == 0


def test_llm_and_stt_boards_do_not_mix(clock):
    fake = FakeRedis(clock=lambda: clock[0])
    llm = shared.SharedCooldownBoard("llm", redis=lambda: fake, clock=lambda: clock[0])
    stt = shared.SharedCooldownBoard("stt", redis=lambda: fake, clock=lambda: clock[0])
    llm.cool("slot1", seconds=60)
    assert stt.remaining("slot1") == 0


def test_board_without_redis_or_with_redis_down_still_works(clock):
    fake = FakeRedis(clock=lambda: clock[0])
    board = shared.SharedCooldownBoard("llm", redis=lambda: fake, clock=lambda: clock[0])
    fake.down = True
    board.cool("slot1", seconds=30)
    assert board.remaining("slot1") == pytest.approx(30)
    local = shared.SharedCooldownBoard("llm", redis=lambda: None, clock=lambda: clock[0])
    local.cool("slot2", seconds=5)
    assert local.remaining("slot2") == pytest.approx(5)
    assert isinstance(local.memory, CooldownBoard)


# --- picking the backend -----------------------------------------------------

def test_dead_redis_url_is_detected_and_probed_again_later(monkeypatch):
    shared.reset_shared_redis()
    monkeypatch.setattr(shared.settings, "redis_url", "redis://localhost:1/0")
    assert shared.shared_redis() is None
    # not probed on every call
    monkeypatch.setattr(shared, "_connect", lambda url: pytest.fail("probed again too soon"))
    assert shared.shared_redis() is None
    shared.reset_shared_redis()


def test_reachable_redis_is_used(monkeypatch):
    fake = FakeRedis()
    shared.reset_shared_redis()
    monkeypatch.setattr(shared.settings, "redis_url", "redis://example:6379/0")
    monkeypatch.setattr(shared, "_connect", lambda url: fake)
    assert shared.shared_redis() is fake
    assert "ping" in fake.calls
    shared.reset_shared_redis()
