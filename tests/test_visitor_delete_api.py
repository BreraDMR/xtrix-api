"""Deleting visitor identity never deletes salon records or revives spent proofs."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Event

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, select, text, update
from sqlalchemy.orm import Session

from app.api.core import visitor_challenges, visitor_delete, visitor_recent_auth, visitor_sessions
from app.api.core.clock import utcnow
from app.api.core.security import get_password_hash
from app.db.models.client import Client
from app.db.models.visit import Visit
from app.db.models.visitor_access import VisitorClaim, VisitorGrant
from app.db.models.visitor_account import VisitorAccount, VisitorGoogleIdentity
from app.db.models.visitor_auth import VisitorChallenge, VisitorSession
from app.db.models.visitor_proof_mail_job import VisitorProofMailJob
from tests import test_visitor_history as base

world = base.world
PASSWORD = 'Visitor!Delete-pass-26'


@pytest.fixture
def deletion(db, world, monkeypatch):
    now = utcnow()
    clock = [now]
    monkeypatch.setattr(visitor_sessions, 'utcnow', lambda: clock[0])
    monkeypatch.setattr(visitor_recent_auth, 'utcnow', lambda: clock[0])
    monkeypatch.setattr(visitor_delete, 'utcnow', lambda: clock[0])
    owner = db.get(VisitorAccount, world['one'])
    owner.hashed_password = get_password_hash(PASSWORD)
    db.add(VisitorGoogleIdentity(account_id=owner.id, subject='deleted-google-identity'))
    db.flush()
    sessions = [visitor_sessions.create_session(db, db.get(VisitorAccount, world[key]))[1]
                for key in ['one', 'one', 'two']]
    grant = base.grant(db, world)
    foreign_grant = base.grant(db, world, actor='two', business='b')
    claim = VisitorClaim(kind='client_invite', proof_hash='a' * 64,
        business_id=world['a'], client_id=world['client'], recipient_email=owner.email_canonical,
        consumed_at=now, consumed_account_id=owner.id, grant_id=grant,
        created_at=now, expires_at=now + timedelta(days=1))
    db.add(claim)
    proof = visitor_challenges.issue_challenge(db, 'reset', account_id=owner.id,
                                             email=owner.email_canonical, now=now)
    db.add(VisitorProofMailJob(challenge_id=proof.row.id, purpose='reset', locale='en',
        origin='https://example.com', status='pending', attempts=0, key_id='test',
        nonce=b'nonce', ciphertext=b'private-proof', next_attempt_at=now,
        deadline=now + timedelta(hours=1), created_at=now, updated_at=now))
    db.commit()
    return {**world, 'sessions': sessions, 'clock': clock, 'claim': claim.id,
            'foreign_grant': foreign_grant, 'email': owner.email_canonical, 'proof': proof.raw_token}


def private(response, status):
    assert response.status_code == status, response.text
    assert response.headers['cache-control'] == 'no-store'


def test_delete_removes_identity_and_all_devices_but_keeps_salon_data(client, db, deletion):
    before = {model: list(db.execute(select(model.__table__).order_by(model.id)).mappings())
              for model in (Client, Visit)}
    response = client.delete('/visitor/me', headers={'X-Visitor-Token': deletion['sessions'][0]})
    private(response, 204)
    assert response.content == b''
    db.expire_all()
    assert db.get(VisitorAccount, deletion['one']) is None
    for model in (VisitorGoogleIdentity, VisitorSession, VisitorChallenge, VisitorGrant):
        assert db.query(model).filter(model.account_id == deletion['one']).count() == 0
    assert db.query(VisitorProofMailJob).count() == 0
    spent = db.get(VisitorClaim, deletion['claim'])
    assert spent.consumed_at is not None and spent.proof_hash == 'a' * 64
    assert spent.consumed_account_id is None and spent.grant_id is None
    assert spent.recipient_email != deletion['email']
    assert db.get(VisitorGrant, deletion['foreign_grant']) is not None
    assert db.get(VisitorAccount, deletion['two']).email_canonical == 'other@example.com'
    for model, rows in before.items():
        assert list(db.execute(select(model.__table__).order_by(model.id)).mappings()) == rows
    for raw in deletion['sessions'][:2]:
        private(client.get('/visitor/me', headers={'X-Visitor-Token': raw}), 401)
    private(client.get('/visitor/me', headers={'X-Visitor-Token': deletion['sessions'][2]}), 200)
    private(client.post('/visitor/auth/login', json={'email': deletion['email'], 'password': PASSWORD}), 401)
    private(client.post('/visitor/auth/password/reset/confirm',
        json={'token': deletion['proof'], 'new_password': PASSWORD}), 400)


def test_same_email_can_register_again_without_old_grants(client, db, deletion):
    private(client.delete('/visitor/me', headers={'X-Visitor-Token': deletion['sessions'][0]}), 204)
    proof = visitor_challenges.issue_challenge(db, 'register', email=deletion['email'])
    db.commit()
    response = client.post('/visitor/auth/register/complete',
                           json={'token': proof.raw_token, 'new_password': PASSWORD})
    private(response, 201)
    new_token = response.json()['session_token']
    profile = client.get('/visitor/me', headers={'X-Visitor-Token': new_token})
    private(profile, 200)
    assert profile.json()['id'] != deletion['one']
    private(client.get('/visitor/visits', headers={'X-Visitor-Token': new_token}), 200)
    assert client.get('/visitor/visits', headers={'X-Visitor-Token': new_token}).json()['entries'] == []


@pytest.mark.parametrize('headers', [{}, {'Authorization': 'Bearer unrelated-crm-token'},
                                    {'X-Visitor-Token': 'vst_' + 'A' * 43}])
def test_missing_or_wrong_authority_cannot_delete(client, db, deletion, headers):
    private(client.delete('/visitor/me', headers=headers), 401)
    assert db.get(VisitorAccount, deletion['one']) is not None


@pytest.mark.parametrize('age', [timedelta(minutes=5), timedelta(minutes=6), timedelta(seconds=-1)])
def test_requires_recent_authentication(client, db, deletion, age):
    deletion['clock'][0] += age
    private(client.delete('/visitor/me', headers={'X-Visitor-Token': deletion['sessions'][0]}), 403)
    assert db.get(VisitorAccount, deletion['one']) is not None


def test_body_and_query_cannot_target_another_account(client, db, deletion):
    response = client.request('DELETE', f"/visitor/me?account_id={deletion['two']}",
        headers={'X-Visitor-Token': deletion['sessions'][0]}, json={'account_id': deletion['two']})
    private(response, 204)
    db.expire_all()
    assert db.get(VisitorAccount, deletion['two']) is not None


def test_deletion_also_erases_old_registration_proofs_for_this_email(client, db, deletion):
    old = visitor_challenges.issue_challenge(db, 'register', email=deletion['email'])
    foreign = visitor_challenges.issue_challenge(db, 'register', email='other@example.com')
    old_id, foreign_id, old_raw = old.row.id, foreign.row.id, old.raw_token
    db.commit()
    private(client.delete('/visitor/me', headers={'X-Visitor-Token': deletion['sessions'][0]}), 204)
    db.expire_all()
    assert db.get(VisitorChallenge, old_id) is None
    assert db.get(VisitorChallenge, foreign_id) is not None
    private(client.post('/visitor/auth/register/complete',
        json={'token': old_raw, 'new_password': PASSWORD}), 400)


def test_spent_invitation_cannot_restore_access_after_reregistration(client, db, deletion):
    from app.api.core import visitor_client_invite_accept
    raw = 'vci_' + 'A' * 43
    db.get(VisitorClaim, deletion['claim']).proof_hash = visitor_client_invite_accept._digest(raw)
    db.commit()
    private(client.delete('/visitor/me', headers={'X-Visitor-Token': deletion['sessions'][0]}), 204)
    proof = visitor_challenges.issue_challenge(db, 'register', email=deletion['email'])
    db.commit()
    registered = client.post('/visitor/auth/register/complete',
        json={'token': proof.raw_token, 'new_password': PASSWORD})
    private(registered, 201)
    response = client.post('/visitor/claims/client-invite', json={'token': raw},
        headers={'X-Visitor-Token': registered.json()['session_token']})
    private(response, 400)


@pytest.mark.parametrize('change', ['revoke', 'age', 'delete', 'retarget'])
def test_deletion_rechecks_actor_after_waiting_for_account_lock(client, db, deletion, monkeypatch, change):
    resolved = Event()
    original = visitor_sessions.get_valid_session
    def observe(session, raw):
        row = original(session, raw)
        if row is not None:
            resolved.set()
        return row
    monkeypatch.setattr(visitor_sessions, 'get_valid_session', observe)
    with Session(db.get_bind()) as blocker:
        visitor_sessions.lock_verified_account(blocker, deletion['one'])
        with ThreadPoolExecutor(max_workers=1) as executor:
            pending = executor.submit(client.delete, '/visitor/me',
                headers={'X-Visitor-Token': deletion['sessions'][0]})
            try:
                assert resolved.wait(timeout=5)
                if change == 'revoke':
                    visitor_sessions.revoke_all_sessions(blocker, account_id=deletion['one'])
                elif change == 'age':
                    deletion['clock'][0] += timedelta(minutes=5)
                elif change == 'retarget':
                    blocker.execute(update(VisitorSession).where(VisitorSession.account_id == deletion['one'])
                        .values(account_id=deletion['two']))
                else:
                    blocker.execute(text('DELETE FROM visitor_accounts WHERE id = :id'), {'id': deletion['one']})
                blocker.commit()
            finally:
                blocker.rollback()
            response = pending.result(timeout=10)
    private(response, 403 if change == 'age' else 401)
    db.expire_all()
    assert db.get(VisitorAccount, deletion['two']) is not None
    if change != 'delete':
        assert db.get(VisitorAccount, deletion['one']) is not None


def test_expiry_during_cascade_rolls_back_every_change(client, db, deletion):
    engine = db.get_bind()
    def after_delete(_conn, _cursor, statement, *_args):
        if statement.startswith('DELETE FROM visitor_accounts'):
            deletion['clock'][0] += timedelta(minutes=5)
    event.listen(engine, 'after_cursor_execute', after_delete)
    try:
        response = client.delete('/visitor/me', headers={'X-Visitor-Token': deletion['sessions'][0]})
    finally:
        event.remove(engine, 'after_cursor_execute', after_delete)
    private(response, 403)
    db.expire_all()
    assert db.get(VisitorAccount, deletion['one']) is not None
    assert db.query(VisitorProofMailJob).count() == 1
    assert db.get(VisitorClaim, deletion['claim']).recipient_email == deletion['email']


def test_real_commit_failure_rolls_back_identity_queue_and_links(db, deletion):
    from app.main import app
    db.execute(text("""CREATE FUNCTION deletion_commit_failure() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN RAISE EXCEPTION 'forced account deletion failure'; END $$"""))
    db.execute(text("""CREATE CONSTRAINT TRIGGER deletion_commit_failure
        AFTER DELETE ON visitor_accounts DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
        EXECUTE FUNCTION deletion_commit_failure()"""))
    db.commit()
    try:
        with TestClient(app, raise_server_exceptions=False) as failing:
            response = failing.delete('/visitor/me', headers={'X-Visitor-Token': deletion['sessions'][0]})
        private(response, 500)
        assert 'forced account deletion failure' not in response.text
        db.expire_all()
        assert db.get(VisitorAccount, deletion['one']) is not None
        assert db.query(VisitorProofMailJob).count() == 1
        assert db.get(VisitorClaim, deletion['claim']).recipient_email == deletion['email']
        assert visitor_sessions.get_valid_session(db, deletion['sessions'][0]) is not None
    finally:
        db.rollback()
        db.execute(text('DROP TRIGGER deletion_commit_failure ON visitor_accounts'))
        db.execute(text('DROP FUNCTION deletion_commit_failure()'))
        db.commit()
