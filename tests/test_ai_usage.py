"""ai_usage rows and the monthly limit per subscription plan."""
from datetime import datetime, timedelta

import pytest

from app.ai.config import load_ai_config
from app.ai.errors import AILimitReached
from app.ai.usage import check_limit, monthly_limit, record_usage, used_this_month
from app.api.core.clock import utcnow
from app.api.crud import crud_employee, crud_subscription
from app.db.models.ai_usage import AIUsage
from app.db.models.business import Business
from app.schemas.employee import EmployeeCreate

CFG = load_ai_config({"AI_MONTHLY_LIMITS": "trial_admin=3,solo_pro=7", "AI_MONTHLY_LIMIT_DEFAULT": "5"})


def owner_with_business(db, make_user):
    owner = make_user("business_owner")
    biz = Business(name="Salon", business_type="salon")
    db.add(biz)
    db.commit()
    owner.business_id = biz.id
    db.commit()
    return owner


def record(db, user, **kw):
    data = dict(provider="groq", slot_index=2, model="m", role="fast",
                prompt_tokens=100, completion_tokens=20, cost_estimate=0.0001, succeeded=True)
    data.update(kw)
    return record_usage(db, user, **data)


def test_record_keeps_ids_and_numbers_never_keys(db, make_user):
    owner = owner_with_business(db, make_user)
    row = record(db, owner)
    assert (row.business_id, row.user_id, row.provider, row.slot_index) == (owner.business_id, owner.id, "groq", 2)
    assert (row.prompt_tokens, row.completion_tokens, row.succeeded) == (100, 20, True)
    assert row.created_at is not None
    columns = {c.name for c in AIUsage.__table__.columns}
    assert not any("key" in c for c in columns)


def test_limit_by_plan_code_with_env_override(db, make_user):
    owner = owner_with_business(db, make_user)  # gets trial_admin on signup
    assert monthly_limit(db, owner, CFG) == 3
    solo = make_user("specialist")  # trial_specialist: not in the override, has a built-in default
    assert monthly_limit(db, solo, CFG) == 50


def test_unknown_plan_gets_the_default(db, make_user):
    owner = owner_with_business(db, make_user)
    sub = crud_subscription.get_active_subscription(db, owner.id)
    sub.plan.code = "some_new_plan"
    db.commit()
    assert monthly_limit(db, owner, CFG) == 5


def test_no_or_expired_subscription_means_zero(db, make_user):
    owner = owner_with_business(db, make_user)
    sub = crud_subscription.get_active_subscription(db, owner.id)
    sub.current_period_end = utcnow() - timedelta(seconds=1)
    db.commit()
    assert monthly_limit(db, owner, CFG) == 0
    sub.status = "cancelled"
    db.commit()
    assert monthly_limit(db, owner, CFG) == 0
    with pytest.raises(AILimitReached) as exc:
        check_limit(db, owner, CFG)
    assert exc.value.limit == 0


def test_limit_counts_the_whole_business_this_month(db, make_user):
    owner = owner_with_business(db, make_user)
    staff = make_user("specialist")
    crud_employee.create_employee(db, EmployeeCreate(employee_user_id=staff.id), owner.id)
    record(db, owner)
    record(db, staff)  # staff spends the owner's allowance
    old = record(db, owner)
    old.created_at = datetime(2020, 1, 1)
    db.commit()
    assert used_this_month(db, owner) == 2
    assert check_limit(db, staff, CFG) == 1  # requests left
    record(db, staff, succeeded=False)  # a failed call burnt tokens too
    with pytest.raises(AILimitReached) as exc:
        check_limit(db, owner, CFG)
    assert (exc.value.limit, exc.value.used) == (3, 3)
    assert 0 < exc.value.retry_after <= 31 * 24 * 3600


def test_other_business_usage_does_not_count(db, make_user):
    a = owner_with_business(db, make_user)
    b = owner_with_business(db, make_user)
    for _ in range(3):
        record(db, a)
    assert used_this_month(db, b) == 0
    assert check_limit(db, b, CFG) == 3


def test_solo_specialist_counts_own_rows(db, make_user):
    solo = make_user("specialist")
    other = make_user("specialist")
    record(db, solo)
    record(db, other)
    assert used_this_month(db, solo) == 1


def test_migration_goes_down_and_up():
    from alembic.config import Config
    from sqlalchemy import inspect

    from alembic import command
    from app.db.session import engine

    cfg = Config("alembic.ini")
    command.downgrade(cfg, "0041_service_description")
    try:
        assert "ai_usage" not in inspect(engine).get_table_names()
    finally:
        command.upgrade(cfg, "head")
    assert "ai_usage" in inspect(engine).get_table_names()
