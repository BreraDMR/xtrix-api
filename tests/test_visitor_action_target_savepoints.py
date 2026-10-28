"""Action facts cannot outlive the savepoint that acquired their row locks."""
import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.api.core import visitor_action_target as target
from app.db.models.visitor_access import VisitorGrant
from tests import test_visitor_action_target as author

world = author.world


def test_rolled_back_savepoint_cannot_revalidate_unlocked_facts(db, world):
    savepoint = db.begin_nested()
    facts = target.resolve_action_target(db, world['raw'], world['visit'])
    root = db.get_transaction()
    savepoint.rollback()
    assert db.get_transaction() is root and root.is_active
    try:
        with Session(db.get_bind()) as foreign_writer:
            # NOWAIT succeeds only because the savepoint's grant lock was released.
            assert foreign_writer.execute(select(VisitorGrant.id).where(VisitorGrant.id == world['grant'])
                                          .with_for_update(nowait=True)).scalar_one() == world['grant']
            with pytest.raises(target.DirtyActionState):
                target.revalidate_action_target(db, world['raw'], facts)
    finally:
        db.rollback()


@pytest.mark.parametrize('finish', ['commit', 'rollback'])
def test_facts_bind_active_innermost_transaction_and_fail_closed_after_finish(db, world, finish):
    savepoint = db.begin_nested()
    facts = target.resolve_action_target(db, world['raw'], world['visit'])
    assert facts._transaction is savepoint
    assert target.revalidate_action_target(db, world['raw'], facts).grant_id == world['grant']
    getattr(savepoint, finish)()
    assert db.get_transaction().is_active
    with pytest.raises(target.DirtyActionState):
        target.revalidate_action_target(db, world['raw'], facts)
    db.rollback()


def test_outer_facts_and_locks_survive_later_independent_savepoint_rollback(db, world):
    facts = target.resolve_action_target(db, world['raw'], world['visit'])
    root = db.get_transaction()
    savepoint = db.begin_nested()
    db.execute(text('SELECT 1'))
    savepoint.rollback()
    assert facts._transaction is root and root.is_active
    try:
        with Session(db.get_bind()) as foreign_writer:
            with pytest.raises(DBAPIError) as caught:
                foreign_writer.execute(select(VisitorGrant.id).where(VisitorGrant.id == world['grant'])
                                       .with_for_update(nowait=True))
            assert caught.value.orig.pgcode == '55P03'
            foreign_writer.rollback()
        assert target.revalidate_action_target(db, world['raw'], facts).grant_id == world['grant']
    finally:
        db.rollback()
