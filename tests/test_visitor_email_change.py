"""Changing verified contact revokes visitor credentials, never their ownership."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timedelta
from threading import Barrier, Event
import hashlib
import time

import pytest
from sqlalchemy import delete, event, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.core import visitor_challenges, visitor_recent_auth, visitor_sessions
from app.api.core import visitor_email_change as core
from app.db.models.visitor_account import VisitorAccount, VisitorGoogleIdentity
from app.db.models.visitor_auth import VisitorChallenge, VisitorSession
from app.db.models.visitor_access import VisitorGrant
from app.db.models.user import User
from app.db.models.client import Client
from app.db.models.business import Business
from app.db.models.notification_outbox import NotificationOutbox
from app.db.models.subscription import Subscription
from tests.test_visitor_access_models import seed_access_world

NOW = datetime(2026,10,27,12)
NEW_EMAIL = 'New.Contact+Tag@example.com'


@pytest.fixture
def world(db,monkeypatch):
    state=seed_access_world(db)
    state['clock']=[NOW]
    for module in (visitor_challenges,visitor_recent_auth,visitor_sessions,core):
        monkeypatch.setattr(module,'utcnow',lambda:state['clock'][0])
    owner=visitor_sessions.lock_verified_account(db,state['one'])
    state['old_email']=owner.email_canonical
    owner.hashed_password='preserved-visitor-password-canary'
    db.add_all([VisitorGoogleIdentity(subject='email-change-own-principal',account_id=state['one']),
                VisitorGoogleIdentity(subject='email-change-other-principal',account_id=state['two'])])
    db.flush()
    state['sessions']=[];state['session_ids']=[]
    for account in (owner,owner,db.get(VisitorAccount,state['two'])):
        row,raw=visitor_sessions.create_session(db,account)
        state['sessions'].append(raw);state['session_ids'].append(row.id)
    db.add(VisitorGrant(account_id=state['one'],business_id=state['business'],client_id=state['client'],scope='visit',visit_id=state['visit']))
    db.commit()
    return state


def test_confirm_rollback_preserves_email_proof_and_all_sessions(db,world):
    prepared=core.issue(db,world['sessions'][0],NEW_EMAIL)
    db.commit()
    result=core.confirm(db,world['sessions'][0],prepared.raw_token)
    assert result.account_id==world['one'] and result.revoked_sessions==2
    db.rollback();db.expire_all()
    assert db.get(VisitorAccount,world['one']).email_canonical==world['old_email']
    assert db.get(VisitorChallenge,prepared.challenge_id).consumed_at is None
    assert all(db.get(VisitorSession,ident).revoked_at is None for ident in world['session_ids'])


def proof(db,world,email=NEW_EMAIL,session=None):
    prepared=core.issue(db,session or world['sessions'][0],email)
    db.commit()
    return prepared


def unchanged(db,world,challenge_ids):
    db.expire_all()
    account=db.get(VisitorAccount,world['one'])
    assert account.email_canonical==world['old_email']
    assert account.hashed_password=='preserved-visitor-password-canary'
    assert all(db.get(VisitorChallenge,key).consumed_at is None for key in challenge_ids)
    assert all(db.get(VisitorSession,key).revoked_at is None for key in world['session_ids'])
    assert db.query(VisitorSession).count()==3


def test_issue_returns_private_proof_only_and_preserves_canonical_dots_plus(db,world):
    before=[db.query(model).count() for model in (VisitorAccount,User,Business,VisitorGrant,VisitorSession,NotificationOutbox)]
    prepared=core.issue(db,world['sessions'][0],'  New.Contact+Tag@EXAMPLE.COM  ')
    assert len(prepared.raw_token)==47 and prepared.raw_token.startswith('vch_')
    assert prepared.expires_at==NOW+timedelta(minutes=30)
    assert repr(prepared)=='PreparedEmailChange()'
    row=db.get(VisitorChallenge,prepared.challenge_id)
    assert row.purpose=='email_change' and row.account_id==world['one'] and row.origin is None
    assert row.email_canonical=='new.contact+tag@example.com'
    assert row.token_hash==hashlib.sha256(prepared.raw_token.encode()).hexdigest()
    assert row.consumed_at is None
    with Session(db.get_bind()) as observer:
        assert observer.get(VisitorChallenge,prepared.challenge_id) is None
    assert [db.query(model).count() for model in (VisitorAccount,User,Business,VisitorGrant,VisitorSession,NotificationOutbox)]==before
    db.rollback()


def test_success_revokes_only_owner_sessions_and_invalidates_only_relevant_siblings(db,world,monkeypatch):
    from app.api.core import subscription_access
    monkeypatch.setattr(subscription_access,'enforce_business_access',lambda *_a,**_k:pytest.fail('CRM billing gate'))
    monkeypatch.setattr(subscription_access,'enforce_subscription_mutation_access',lambda *_a,**_k:pytest.fail('CRM billing gate'))
    prepared=proof(db,world)
    sibling=proof(db,world,'second.target@example.com')
    reset=visitor_challenges.issue_challenge(db,'reset',account_id=world['one'],email=world['old_email'])
    foreign=visitor_challenges.issue_challenge(db,'reset',account_id=world['two'],email='owner.two@example.com')
    google=visitor_challenges.issue_challenge(db,'google_link',account_id=world['one'],origin='https://visitor.example')
    register=visitor_challenges.issue_challenge(db,'register',email=world['old_email'])
    ids={'reset':reset.row.id,'foreign':foreign.row.id,'google':google.row.id,'register':register.row.id}
    db.commit()
    before={model:db.query(model).count() for model in (VisitorAccount,VisitorGoogleIdentity,VisitorGrant,User,Client,Business,Subscription,NotificationOutbox)}
    result=core.confirm(db,world['sessions'][0],prepared.raw_token)
    assert result.account_id==world['one'] and result.revoked_sessions==2
    assert NEW_EMAIL not in repr(result) and world['sessions'][0] not in repr(result)
    with Session(db.get_bind()) as observer:
        assert observer.get(VisitorAccount,world['one']).email_canonical==world['old_email']
        assert observer.get(VisitorSession,world['session_ids'][0]).revoked_at is None
    db.commit();db.expire_all()
    account=db.get(VisitorAccount,world['one'])
    assert account.email_canonical=='new.contact+tag@example.com' and account.email_verified_at==NOW
    assert account.hashed_password=='preserved-visitor-password-canary'
    assert {row.subject:row.account_id for row in db.query(VisitorGoogleIdentity)}=={
        'email-change-own-principal':world['one'],'email-change-other-principal':world['two']}
    assert db.query(VisitorGrant).one().account_id==world['one']
    assert db.get(Client,world['client']).contact_email==world['old_email']
    for ident in [prepared.challenge_id,sibling.challenge_id,ids['reset']]:
        assert db.get(VisitorChallenge,ident).consumed_at==NOW
    for ident in [ids['foreign'],ids['google'],ids['register']]:
        assert db.get(VisitorChallenge,ident).consumed_at is None
    assert all(visitor_sessions.get_valid_session(db,raw) is None for raw in world['sessions'][:2])
    assert visitor_sessions.get_valid_session(db,world['sessions'][2]) is not None
    assert before=={model:db.query(model).count() for model in before}
    assert db.query(VisitorSession).count()==3


@pytest.mark.parametrize('email',[None,True,12,'','not-email','a b@example.com','x'*321+'@example.com'])
def test_invalid_recipient_does_not_echo_input_or_issue_proof(db,world,email):
    with pytest.raises(core.InvalidEmailChangeRecipient) as error:core.issue(db,world['sessions'][0],email)
    assert str(error.value)=='A valid new email is required'
    assert db.query(VisitorChallenge).count()==0


@pytest.mark.parametrize('purpose',['register','reset','google_login','google_link'])
def test_wrong_purpose_cannot_change_email(db,world,purpose):
    args={'email':world['old_email']} if purpose in {'register','reset'} else {'origin':'https://visitor.example'}
    if purpose in {'reset','google_link'}:args['account_id']=world['one']
    issued=visitor_challenges.issue_challenge(db,purpose,**args)
    raw,ident=issued.raw_token,issued.row.id;db.commit()
    with pytest.raises(core.InvalidEmailChangeProof):core.confirm(db,world['sessions'][0],raw)
    db.rollback();unchanged(db,world,[ident])


@pytest.mark.parametrize('raw',[None,'','vci_'+'A'*43,'vch_'+'A'*43,'vch_'+'A'*42+'B'])
def test_invalid_or_unknown_proof_does_not_mutate_account(db,world,raw):
    with pytest.raises(core.InvalidEmailChangeProof):core.confirm(db,world['sessions'][0],raw)
    db.rollback();unchanged(db,world,[])


def test_other_account_cannot_consume_target_proof(db,world):
    prepared=proof(db,world)
    with pytest.raises(core.InvalidEmailChangeProof):core.confirm(db,world['sessions'][2],prepared.raw_token)
    db.rollback();unchanged(db,world,[prepared.challenge_id])


@pytest.mark.parametrize('change',['future_session','exact_five_minutes','expired_session','revoked_session'])
def test_issue_and_confirm_require_current_recent_actor(db,world,change):
    prepared=proof(db,world)
    session=db.get(VisitorSession,world['session_ids'][0])
    if change=='future_session':session.created_at=NOW+timedelta(microseconds=1)
    elif change=='exact_five_minutes':session.created_at=NOW-timedelta(minutes=5)
    elif change=='expired_session':
        session.created_at=NOW-timedelta(minutes=1);session.expires_at=NOW
    else:session.revoked_at=NOW
    db.commit()
    with pytest.raises(visitor_recent_auth.VisitorReauthenticationRequired):core.issue(db,world['sessions'][0],NEW_EMAIL)
    db.rollback()
    with pytest.raises(visitor_recent_auth.VisitorReauthenticationRequired):core.confirm(db,world['sessions'][0],prepared.raw_token)
    db.rollback()
    assert db.get(VisitorChallenge,prepared.challenge_id).consumed_at is None
    assert db.get(VisitorAccount,world['one']).email_canonical==world['old_email']


def test_collision_with_another_google_account_is_generic_and_rolls_back_everything(db,world):
    prepared=proof(db,world,'owner.two@example.com')
    with pytest.raises(core.EmailChangeConflict) as error:core.confirm(db,world['sessions'][0],prepared.raw_token)
    assert str(error.value)=='Email change could not be completed'
    assert 'owner.two@example.com' not in str(error.value) and str(world['two']) not in str(error.value)
    db.rollback();unchanged(db,world,[prepared.challenge_id])
    assert db.query(VisitorGoogleIdentity).count()==2
    assert db.query(VisitorGoogleIdentity).filter_by(subject='email-change-other-principal').one().account_id==world['two']


def test_actual_deferred_commit_failure_restores_proofs_email_and_credentials(db,world):
    prepared=proof(db,world)
    sibling=proof(db,world,'sibling.target@example.com')
    core.confirm(db,world['sessions'][0],prepared.raw_token)
    db.execute(text('CREATE TEMP TABLE email_change_commit_failure (n int UNIQUE DEFERRABLE INITIALLY DEFERRED)'))
    db.execute(text('INSERT INTO email_change_commit_failure VALUES (1),(1)'))
    with pytest.raises(IntegrityError):db.commit()
    db.rollback();unchanged(db,world,[prepared.challenge_id,sibling.challenge_id])
    core.confirm(db,world['sessions'][0],prepared.raw_token);db.commit()
    assert db.get(VisitorAccount,world['one']).email_canonical=='new.contact+tag@example.com'


def test_spent_email_proof_and_old_reset_cannot_restore_previous_contact(db,world):
    prepared=proof(db,world)
    old_reset=visitor_challenges.issue_challenge(db,'reset',account_id=world['one'],email=world['old_email'])
    reset_raw,reset_id=old_reset.raw_token,old_reset.row.id;db.commit()
    core.confirm(db,world['sessions'][0],prepared.raw_token);db.commit()
    actor=visitor_sessions.lock_verified_account(db,world['one'])
    _,new_session=visitor_sessions.create_session(db,actor);db.commit()
    with pytest.raises(core.InvalidEmailChangeProof):core.confirm(db,new_session,prepared.raw_token)
    db.rollback()
    back=proof(db,world,world['old_email'],session=new_session)
    core.confirm(db,new_session,back.raw_token);db.commit()
    from app.api.core import visitor_password_reset
    with pytest.raises(visitor_password_reset.InvalidResetProof):visitor_password_reset.confirm_reset(db,reset_raw,'Safe!Visitor-57-Pass')
    db.rollback()
    assert db.get(VisitorAccount,world['one']).email_canonical==world['old_email']
    assert db.get(VisitorChallenge,reset_id).consumed_at is not None
    assert db.get(VisitorAccount,world['one']).hashed_password=='preserved-visitor-password-canary'


@contextmanager
def real_wait(engine,matching):
    reached,pid=Event(),[]
    def observe(conn,_cursor,statement,*_args):
        if matching(statement):
            pid[:]=[conn.connection.driver_connection.get_backend_pid()];reached.set()
    event.listen(engine,'before_cursor_execute',observe)
    try:
        def blocked(holder_pid):
            assert reached.wait(5),'Worker missed expected SQL'
            until=time.monotonic()+5
            with engine.connect() as observer:
                while time.monotonic()<until:
                    if holder_pid in observer.execute(text('SELECT pg_blocking_pids(:pid)'),{'pid':pid[0]}).scalar_one():return
                    time.sleep(.01)
            pytest.fail('Expected real PostgreSQL lock wait')
        yield blocked
    finally:event.remove(engine,'before_cursor_execute',observe)


def confirm_attempt(engine,raw_session,raw_proof):
    with Session(engine,autoflush=False) as worker:
        try:
            result=core.confirm(worker,raw_session,raw_proof);worker.commit()
            return 'success',result.account_id
        except (core.InvalidEmailChangeProof,core.EmailChangeConflict,
                visitor_recent_auth.VisitorReauthenticationRequired) as error:
            worker.rollback();return type(error).__name__,None


def issue_attempt(engine,raw_session):
    with Session(engine,autoflush=False) as worker:
        try:
            prepared=core.issue(worker,raw_session,NEW_EMAIL);worker.commit()
            return 'success',prepared.challenge_id
        except visitor_recent_auth.VisitorReauthenticationRequired as error:
            worker.rollback();return type(error).__name__,None


@pytest.mark.parametrize('operation',['issue','confirm'])
def test_account_wait_does_not_extend_original_five_minute_authentication(db,world,operation):
    prepared=proof(db,world) if operation=='confirm' else None
    engine=db.get_bind()
    with Session(engine) as holder:
        visitor_sessions.lock_verified_account(holder,world['one'])
        holder_pid=holder.execute(text('SELECT pg_backend_pid()')).scalar_one()
        with real_wait(engine,lambda s:'FROM visitor_accounts' in s and 'FOR UPDATE' in s) as blocked:
            with ThreadPoolExecutor(max_workers=1) as pool:
                future=pool.submit(confirm_attempt,engine,world['sessions'][0],prepared.raw_token) if prepared else pool.submit(issue_attempt,engine,world['sessions'][0])
                try:
                    blocked(holder_pid);world['clock'][0]=NOW+timedelta(minutes=5)
                    holder.rollback()
                    assert future.result(timeout=10)==('VisitorReauthenticationRequired',None)
                finally:holder.rollback()
    unchanged(db,world,[prepared.challenge_id] if prepared else [])
    assert db.query(VisitorChallenge).count()==(1 if prepared else 0)


def test_challenge_wait_rechecks_recent_actor_before_changing_contact(db,world):
    prepared=proof(db,world);engine=db.get_bind()
    with Session(engine) as holder:
        holder.query(VisitorChallenge).filter_by(id=prepared.challenge_id).with_for_update().one()
        holder_pid=holder.execute(text('SELECT pg_backend_pid()')).scalar_one()
        with real_wait(engine,lambda s:'FROM visitor_challenges' in s and 'FOR UPDATE' in s) as blocked:
            with ThreadPoolExecutor(max_workers=1) as pool:
                future=pool.submit(confirm_attempt,engine,world['sessions'][0],prepared.raw_token)
                try:
                    blocked(holder_pid);world['clock'][0]=NOW+timedelta(minutes=5)
                    holder.rollback()
                    assert future.result(timeout=10)==('VisitorReauthenticationRequired',None)
                finally:holder.rollback()
    unchanged(db,world,[prepared.challenge_id])


def test_unique_email_index_wait_that_rolls_back_still_expires_recent_actor(db,world):
    prepared=proof(db,world,'shared.target@example.com');engine=db.get_bind()
    with Session(engine) as holder:
        holder.get(VisitorAccount,world['two']).email_canonical='shared.target@example.com'
        holder.flush();holder_pid=holder.execute(text('SELECT pg_backend_pid()')).scalar_one()
        with real_wait(engine,lambda s:s.startswith('UPDATE visitor_accounts')) as blocked:
            with ThreadPoolExecutor(max_workers=1) as pool:
                future=pool.submit(confirm_attempt,engine,world['sessions'][0],prepared.raw_token)
                try:
                    blocked(holder_pid);world['clock'][0]=NOW+timedelta(minutes=5)
                    holder.rollback()
                    assert future.result(timeout=10)==('VisitorReauthenticationRequired',None)
                finally:holder.rollback()
    unchanged(db,world,[prepared.challenge_id])


@pytest.mark.parametrize('stage,expiry',[('sessions','recent'),('siblings','recent'),('sessions','session'),('siblings','proof')])
def test_late_wait_after_intentional_logout_uses_immutable_deadline_and_rolls_back(db,world,stage,expiry):
    prepared=proof(db,world)
    if expiry=='session':
        db.get(VisitorSession,world['session_ids'][0]).expires_at=NOW+timedelta(minutes=1)
    elif expiry=='proof':
        row=db.get(VisitorChallenge,prepared.challenge_id)
        row.created_at=NOW-timedelta(minutes=29);row.expires_at=NOW+timedelta(minutes=1)
    sibling=visitor_challenges.issue_challenge(db,'reset',account_id=world['one'],email=world['old_email'])
    sibling_id=sibling.row.id;db.commit();engine=db.get_bind()
    with Session(engine) as holder:
        if stage=='sessions':holder.query(VisitorSession).filter_by(id=world['session_ids'][1]).with_for_update().one()
        else:holder.query(VisitorChallenge).filter_by(id=sibling_id).with_for_update().one()
        holder_pid=holder.execute(text('SELECT pg_backend_pid()')).scalar_one()
        needle='UPDATE visitor_sessions' if stage=='sessions' else 'UPDATE visitor_challenges'
        with real_wait(engine,lambda s:s.startswith(needle)) as blocked:
            with ThreadPoolExecutor(max_workers=1) as pool:
                future=pool.submit(confirm_attempt,engine,world['sessions'][0],prepared.raw_token)
                try:
                    blocked(holder_pid)
                    world['clock'][0]=NOW+timedelta(minutes=5 if expiry=='recent' else 1)
                    holder.rollback()
                    expected='InvalidEmailChangeProof' if expiry=='proof' else 'VisitorReauthenticationRequired'
                    assert future.result(timeout=10)==(expected,None)
                finally:holder.rollback()
    unchanged(db,world,[prepared.challenge_id,sibling_id])


def test_issue_unique_proof_wait_rechecks_recent_actor_after_flush(db,world,monkeypatch):
    monkeypatch.setattr(visitor_challenges.secrets,'token_urlsafe',lambda _size:'A'*43)
    engine=db.get_bind()
    with Session(engine) as holder:
        visitor_challenges.issue_challenge(holder,'email_change',account_id=world['two'],email='other.target@example.com')
        holder_pid=holder.execute(text('SELECT pg_backend_pid()')).scalar_one()
        with real_wait(engine,lambda s:s.startswith('INSERT INTO visitor_challenges')) as blocked:
            with ThreadPoolExecutor(max_workers=1) as pool:
                future=pool.submit(issue_attempt,engine,world['sessions'][0])
                try:
                    blocked(holder_pid);world['clock'][0]=NOW+timedelta(minutes=5)
                    holder.rollback()
                    assert future.result(timeout=10)==('VisitorReauthenticationRequired',None)
                finally:holder.rollback()
    unchanged(db,world,[])
    assert db.query(VisitorChallenge).count()==0


def test_two_accounts_claiming_one_new_address_have_one_winner_without_merging(db,world):
    own=proof(db,world,'one.shared.contact@example.com')
    other=proof(db,world,'one.shared.contact@example.com',session=world['sessions'][2])
    initial={row.id:row.email_canonical for row in db.query(VisitorAccount)}
    engine,barrier=db.get_bind(),Barrier(2)
    def overlap(_conn,_cursor,statement,*_args):
        if statement.startswith('UPDATE visitor_accounts') and 'email_canonical' in statement:barrier.wait(timeout=5)
    event.listen(engine,'before_cursor_execute',overlap)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures=[pool.submit(confirm_attempt,engine,world['sessions'][0],own.raw_token),
                     pool.submit(confirm_attempt,engine,world['sessions'][2],other.raw_token)]
            results=[future.result(timeout=10) for future in futures]
    finally:event.remove(engine,'before_cursor_execute',overlap)
    assert sorted(value[0] for value in results)==['EmailChangeConflict','success']
    winner=next(value[1] for value in results if value[0]=='success')
    db.expire_all()
    for row in db.query(VisitorAccount):
        assert row.email_canonical==('one.shared.contact@example.com' if row.id==winner else initial[row.id])
    assert db.query(VisitorAccount).count()==2 and db.query(VisitorGoogleIdentity).count()==2
    assert db.query(VisitorChallenge).filter(VisitorChallenge.consumed_at.is_not(None)).count()==1
    assert {row.subject:row.account_id for row in db.query(VisitorGoogleIdentity)}=={
        'email-change-own-principal':world['one'],'email-change-other-principal':world['two']}
    assert db.query(VisitorGrant).one().account_id==world['one']
