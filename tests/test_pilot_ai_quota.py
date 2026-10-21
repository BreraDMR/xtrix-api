"""A pilot's daily text allowance is reserved before calling an external model."""
from datetime import timedelta
import threading

import pytest

from app.ai.config import AIConfig
from app.ai.errors import AILimitReached
from app.ai import usage
from app.api.core.clock import utcnow
from app.api.core.config import settings
from app.api.crud import crud_employee, crud_subscription, crud_user
from app.db.models.ai_usage import AIUsage
from app.db.models.business import Business
from app.db.session import SessionLocal
from app.schemas.employee import EmployeeCreate


@pytest.fixture(autouse=True)
def pilot_mode(monkeypatch):
    monkeypatch.setattr(settings, "pilot_mode_enabled", True)


def salon(db, make_user):
    owner = make_user("business_owner")
    business = Business(name="Quota salon", business_type="salon")
    db.add(business)
    db.commit()
    owner.business_id = business.id
    db.commit()
    return owner


def history(db, owner, count, **overrides):
    for _ in range(count):
        values = dict(business_id=owner.business_id, user_id=owner.id,
                      provider="test", model="test", role="fast", succeeded=True,
                      created_at=utcnow())
        values.update(overrides)
        db.add(AIUsage(**values))
    db.commit()


def test_daily_allowance_restarts_tomorrow_and_ignores_failed_calls_and_speech(db, make_user):
    owner = salon(db, make_user)
    history(db, owner, 50, created_at=utcnow() - timedelta(days=1))
    history(db, owner, 50, succeeded=False)
    history(db, owner, 50, role="stt")
    # This deliberately tiny legacy monthly allowance must not override the daily pilot tier.
    cfg = AIConfig(monthly_limit_default=1, monthly_limits={"trial_admin": 1})
    row = usage.reserve_request(db, owner, cfg, role="fast")
    assert row.provider == "pending"
    assert usage.pilot_used_today(db, owner) == 1


def test_pending_request_holds_a_daily_slot_and_failure_returns_it(db, make_user):
    owner = salon(db, make_user)
    history(db, owner, 49)
    row = usage.reserve_request(db, owner, AIConfig(), role="fast")
    with pytest.raises(AILimitReached) as exc:
        usage.reserve_request(db, owner, AIConfig(), role="fast")
    assert (exc.value.limit, exc.value.used) == (50, 50)
    assert 0 < exc.value.retry_after <= 86400
    usage.settle(db, row, provider="failed-provider", slot_index=None, model="m",
                 prompt_tokens=12, completion_tokens=0, cost_estimate=0.01, succeeded=False)
    next_row = usage.reserve_request(db, owner, AIConfig(), role="fast")
    assert next_row.id != row.id
    assert usage.pilot_used_today(db, owner) == 50


def test_releasing_a_failed_reservation_does_not_consume_the_allowance(db, make_user):
    solo = make_user("specialist")
    other = make_user("specialist")
    row = usage.reserve_request(db, solo, AIConfig(), role="fast")
    assert usage.pilot_used_today(db, other) == 0
    usage.release(db, row)
    assert usage.pilot_used_today(db, solo) == 0


def test_speech_has_access_checks_but_does_not_spend_another_chat_request(db, make_user):
    owner = salon(db, make_user)
    history(db, owner, 50)
    row = usage.reserve_request(db, owner, AIConfig(), role="stt")
    assert row.role == "stt"
    assert usage.pilot_used_today(db, owner) == 50
    sub = crud_subscription.get_active_subscription(db, owner.id)
    sub.trial_end = utcnow() - timedelta(seconds=1)
    sub.current_period_end = sub.trial_end
    db.commit()
    with pytest.raises(AILimitReached) as exc:
        usage.reserve_request(db, owner, AIConfig(), role="stt")
    assert exc.value.limit == 0


def test_owner_and_staff_racing_for_last_request_share_one_atomic_daily_slot(db, make_user):
    owner = salon(db, make_user)
    staff = make_user("specialist")
    crud_employee.create_employee(db, EmployeeCreate(employee_user_id=staff.id), owner.id)
    history(db, owner, 49)
    barrier = threading.Barrier(2)
    outcomes = []

    def attempt(user_id):
        with SessionLocal() as session:
            try:
                user = crud_user.get_active_user(session, user_id)
                barrier.wait(timeout=5)
                usage.reserve_request(session, user, AIConfig(), role="fast")
                outcomes.append("reserved")
            except AILimitReached:
                outcomes.append("limited")
            except Exception as exc:
                outcomes.append(type(exc).__name__)

    threads = [threading.Thread(target=attempt, args=(uid,)) for uid in (owner.id, staff.id)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
    assert not any(thread.is_alive() for thread in threads)
    assert sorted(outcomes) == ["limited", "reserved"]
    db.expire_all()
    assert usage.pilot_used_today(db, owner) == 50
