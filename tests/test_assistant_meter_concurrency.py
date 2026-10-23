"""Successful concurrent generations must all appear in the billing counter."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import pytest

from app.api.core.ai_metering import consume_ai_unit, get_current_daily_period, get_daily_ai_usage
from app.db.models.business import Business
from app.db.models.usage_counter import UsageCounter
from app.db.session import SessionLocal


@pytest.mark.parametrize("solo", [False, True])
@pytest.mark.parametrize("existing", [False, True])
def test_parallel_generations_increment_one_scope_counter(db, make_user, solo, existing):
    user = make_user()
    business_id = None
    if not solo:
        business = Business(name="Metering salon", business_type="salon")
        db.add(business)
        db.commit()
        business_id = business.id
    user_id = user.id if solo else None
    if existing:
        consume_ai_unit(db, business_id, uuid4(), True, user_id=user_id)
        db.commit()
    gate = Barrier(4)
    db.rollback()

    def consume(_index):
        with SessionLocal() as session:
            # A long-lived request may already have read this row before another
            # successful generation commits. The writer must refresh under lock.
            cached = session.query(UsageCounter).all()
            gate.wait(timeout=10)
            consume_ai_unit(session, business_id, uuid4(), True, user_id=user_id)
            session.commit()
            assert len(cached) == int(existing)

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(consume, range(4)))
    db.expire_all()
    rows = db.query(UsageCounter).filter_by(product="ai", unit="requests", period=get_current_daily_period()).all()
    assert len(rows) == 1
    assert rows[0].count == 4 + int(existing)
    assert get_daily_ai_usage(db, business_id, user_id=user_id) == 4 + int(existing)


def test_missing_scope_cannot_increment_an_unrelated_counter(db, make_user):
    owner = make_user()
    consume_ai_unit(db, None, uuid4(), True, user_id=owner.id)
    db.commit()
    with pytest.raises(ValueError):
        consume_ai_unit(db, None, uuid4(), True)
    db.rollback()
    assert get_daily_ai_usage(db, None, user_id=owner.id) == 1


def test_legacy_solo_counters_keep_their_combined_total(db, make_user):
    owner = make_user()
    business = Business(name="Other scope", business_type="salon")
    db.add(business)
    db.flush()
    for business_id, count in [(None, 2), (None, 3), (business.id, 20)]:
        db.add(UsageCounter(business_id=business_id, user_id=owner.id, product="ai", unit="requests",
                            period=get_current_daily_period(), count=count))
    db.commit()
    assert get_daily_ai_usage(db, None, user_id=owner.id) == 5
    consume_ai_unit(db, None, uuid4(), True, user_id=owner.id)
    db.commit()
    assert get_daily_ai_usage(db, None, user_id=owner.id) == 6
    assert get_daily_ai_usage(db, business.id) == 20
