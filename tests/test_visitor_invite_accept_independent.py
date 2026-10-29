"""Independent probes: the actor must still be live after its final SQL lookup."""
from datetime import timedelta

import pytest
from sqlalchemy import event

from app.api.core import visitor_client_invite_accept as accept
from app.db.models.visitor_auth import VisitorSession
from app.db.models.visitor_access import VisitorClaim, VisitorGrant
from tests.test_visitor_access_models import NOW
from tests import test_visitor_client_invite_accept as original


@pytest.fixture
def world(db, monkeypatch):
    return original.world.__wrapped__(db, monkeypatch)


@pytest.mark.parametrize('spent', [False, True])
def test_final_actor_lookup_crossing_its_deadline_is_rejected(db, world, spent):
    session = db.query(VisitorSession).one()
    session.expires_at = NOW + timedelta(minutes=1)
    db.commit()
    if spent:
        accept.accept_client_invite(db, world['session'], world['raw'])
        db.commit()
    seen = {'updates': 0, 'armed': False, 'advanced': False}
    def advance_after_final_lookup(conn, cursor, statement, parameters, context, executemany):
        if statement.startswith('UPDATE visitor_claims'):
            seen['updates'] += 1
            if seen['updates'] == 1:
                world['clock'][0] = NOW + timedelta(seconds=1)
            if seen['updates'] == 2:
                seen['armed'] = True
        if spent and 'FROM visitor_grants' in statement and 'FOR UPDATE' in statement:
            seen['armed'] = True
        if seen['armed'] and 'FROM visitor_sessions JOIN visitor_accounts' in statement:
            seen['advanced'] = True
            world['clock'][0] = NOW + timedelta(minutes=1)
    event.listen(db.get_bind(), 'after_cursor_execute', advance_after_final_lookup)
    try:
        with pytest.raises(accept.InvalidVisitorSession):
            accept.accept_client_invite(db, world['session'], world['raw'])
        assert seen['advanced'], 'Probe did not cross the real final session SELECT'
    finally:
        event.remove(db.get_bind(), 'after_cursor_execute', advance_after_final_lookup)
        db.rollback()
    if not spent:
        assert db.query(VisitorGrant).count() == 0
        assert db.get(VisitorClaim, world['claim']).consumed_at is None
