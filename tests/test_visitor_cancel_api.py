"""HTTP cancellation acknowledges only a committed, exactly authorized change."""
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, select, text, update
from sqlalchemy.orm import Session

from app.api.core import visitor_cancel, waitlist_service
from app.api.core.i18n import _
from app.db.models.business import Business
from app.db.models.service_payment import ServicePaymentAttempt
from app.db.models.subscription import Subscription
from app.db.models.visit import Visit
from app.main import app
from tests import test_visitor_cancel_core as base

world = base.world


def url(world):
    return f"/visitor/visits/{world['visit']}/cancel"


def headers(world):
    return {'X-Visitor-Token': world['raw'], 'Accept-Language': 'ru'}


def private(response, status):
    assert response.status_code == status, response.text
    assert response.headers['cache-control'] == 'no-store'


def test_http_cancel_commits_before_matching_and_repeat_does_not_match_again(client, db, world, monkeypatch):
    calls = []
    def released(_db, business, service_id, specialist_id, start):
        with Session(db.get_bind()) as observer:
            saved = base.state(observer, world)
            assert saved['visit'][0]['status'] == 'cancelled' and len(saved['events']) == 1
        calls.append((business.id, specialist_id, start))
    monkeypatch.setattr(waitlist_service, 'release_slot_quietly', released)
    response = client.post(url(world), headers=headers(world))
    private(response, 204)
    assert response.content == b''
    with Session(db.get_bind()) as observer:
        first = base.state(observer, world)
    private(client.post(url(world), headers=headers(world)), 204)
    with Session(db.get_bind()) as observer:
        assert base.state(observer, world) == first
    assert len(calls) == 1


@pytest.mark.parametrize('credential', ['missing', 'crm', 'foreign'])
def test_credentials_cannot_substitute_visitor_authority(client, db, world, credential):
    supplied = {'Authorization': 'Bearer unrelated-crm-credential'} if credential == 'crm' else {}
    if credential == 'foreign':
        supplied = {'X-Visitor-Token': world['other_raw']}
    before = base.state(db, world)
    db.rollback()
    private(client.post(url(world), headers=supplied), 404 if credential == 'foreign' else 401)
    with Session(db.get_bind()) as observer:
        assert base.state(observer, world) == before


@pytest.mark.parametrize('policy', ['late', 'unpaid'])
def test_policy_errors_are_human_and_rollback_the_whole_operation(client, db, world, policy):
    if policy == 'late':
        world['clock'][0] += timedelta(days=2, microseconds=1)
        code = 'booking_cancel_too_late'
    else:
        db.get(Subscription, world['sub']).status = 'cancelled'
        db.commit()
        code = 'visitor_actions_unavailable'
    before = base.state(db, world)
    db.rollback()
    response = client.post(url(world), headers=headers(world))
    private(response, 409)
    assert response.json()['error']['message'] == _(code, 'ru')
    with Session(db.get_bind()) as observer:
        assert base.state(observer, world) == before
    private(client.get(f"/visitor/visits/{world['visit']}", headers=headers(world)), 200)


def test_busy_payment_attempt_is_retryable_without_partial_cancellation(client, db, world):
    before = base.state(db, world)
    db.rollback()
    with Session(db.get_bind()) as blocker:
        blocker.execute(select(ServicePaymentAttempt.id).where(ServicePaymentAttempt.id == world['pending'])
                        .with_for_update())
        response = client.post(url(world), headers=headers(world))
        private(response, 409)
        assert response.json()['error']['message'] == _('visitor_action_retry', 'ru')
        with Session(db.get_bind()) as observer:
            assert base.state(observer, world) == before
            observer.execute(text('SELECT id FROM visits WHERE id=:id FOR UPDATE NOWAIT'),
                             {'id': world['visit']})
    private(client.post(url(world), headers=headers(world)), 204)


def test_deferred_commit_failure_returns_no_success_and_rolls_back_everything(db, world, monkeypatch):
    calls = []
    monkeypatch.setattr(waitlist_service, 'release_slot_quietly', lambda *args: calls.append(args))
    db.execute(text("""CREATE FUNCTION reject_http_cancel() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN RAISE EXCEPTION 'synthetic cancellation commit failure'; RETURN NEW; END $$"""))
    db.execute(text('''CREATE CONSTRAINT TRIGGER reject_http_cancel AFTER INSERT ON visit_events
        DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION reject_http_cancel()'''))
    db.commit()
    before = base.state(db, world)
    db.rollback()
    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.post(url(world), headers=headers(world))
        private(response, 500)
        assert world['raw'] not in response.text and not calls
        with Session(db.get_bind()) as observer:
            assert base.state(observer, world) == before
    finally:
        db.rollback()
        db.execute(text('DROP TRIGGER reject_http_cancel ON visit_events'))
        db.execute(text('DROP FUNCTION reject_http_cancel()'))
        db.commit()


@pytest.mark.parametrize('failure', ['matching', 'lookup'])
def test_postcommit_failure_does_not_turn_saved_cancellation_into_http_error(client, db, world, monkeypatch, failure):
    def fail(*args, **kwargs):
        raise RuntimeError('synthetic postcommit failure')
    if failure == 'matching':
        monkeypatch.setattr(waitlist_service, 'release_slot_quietly', fail)
    else:
        original = Session.get
        def get(self, entity, *args, **kwargs):
            if entity is Business:
                fail()
            return original(self, entity, *args, **kwargs)
        monkeypatch.setattr(Session, 'get', get)
    private(client.post(url(world), headers=headers(world)), 204)
    with Session(db.get_bind()) as observer:
        assert observer.get(Visit, world['visit']).status == 'cancelled'


def test_business_deleted_after_cancel_commit_does_not_receive_released_slot_offers(client, db, world, monkeypatch):
    original = visitor_cancel.cancel_visit
    matched = []
    removed = []
    def cancel(session, raw, visit_id):
        result = original(session, raw, visit_id)
        def after_commit(_session):
            with Session(db.get_bind()) as writer:
                writer.execute(update(Business).where(Business.id == world['business'])
                               .values(deleted_at=world['clock'][0]))
                writer.commit()
            removed.append(True)
        event.listen(session, 'after_commit', after_commit, once=True)
        return result
    monkeypatch.setattr(visitor_cancel, 'cancel_visit', cancel)
    monkeypatch.setattr(waitlist_service, 'release_slot_quietly', lambda *args: matched.append(args))
    private(client.post(url(world), headers=headers(world)), 204)
    assert removed == [True] and matched == []
