"""A client invitation is explicit, recipient-bound and rollback-safe."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import timedelta
from threading import Barrier, Event
import time
import hashlib
import secrets

import pytest
from sqlalchemy import delete, event, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.core import visitor_sessions
from app.api.core import visitor_client_invite_accept as accept
from app.db.models.visitor_access import VisitorClaim, VisitorGrant
from app.db.models.visitor_account import VisitorAccount
from app.db.models.visitor_auth import VisitorSession
from app.db.models.user import User
from app.db.models.client import Client
from app.db.models.business import Business
from app.db.models.client_business_relation import ClientBusinessRelation
from app.db.models.notification_outbox import NotificationOutbox
from tests.test_visitor_access_models import NOW, seed_access_world


@pytest.fixture
def world(db, monkeypatch):
    state=seed_access_world(db)
    state['clock']=[NOW]
    monkeypatch.setattr(visitor_sessions,'utcnow',lambda:state['clock'][0])
    monkeypatch.setattr(accept,'utcnow',lambda:state['clock'][0])
    account=visitor_sessions.lock_verified_account(db,state['one'])
    _,state['session']=visitor_sessions.create_session(db,account)
    state['raw']='vci_'+secrets.token_urlsafe(32)
    receipt=VisitorClaim(kind='client_invite',proof_hash=hashlib.sha256(state['raw'].encode()).hexdigest(),
        business_id=state['business'],client_id=state['client'],recipient_email=account.email_canonical,
        issued_by_user_id=state['staff'],created_at=NOW,expires_at=NOW+timedelta(minutes=30))
    db.add(receipt);db.flush();state['claim']=receipt.id;db.commit()
    return state


def test_accept_creates_one_client_grant_without_internal_commit(db, world):
    result=accept.accept_client_invite(db,world['session'],world['raw'])
    assert result.account_id==world['one'] and result.created is True
    assert db.in_transaction()
    db.rollback()
    assert db.query(VisitorGrant).count()==0
    assert db.get(VisitorClaim,world['claim']).consumed_at is None


def run(db, world, raw=None, session=None):
    return accept.accept_client_invite(db, session or world['session'], raw or world['raw'])


def assert_pending(db, world):
    db.expire_all()
    receipt=db.get(VisitorClaim,world['claim'])
    assert receipt.consumed_at is None and receipt.consumed_account_id is None and receipt.grant_id is None
    assert db.query(VisitorGrant).count()==0


def test_success_changes_only_grant_and_receipt_without_billing_or_account_lookup(db, world, monkeypatch):
    from app.api.core import subscription_access
    def forbidden(*_args,**_kwargs):
        pytest.fail('Accept called a CRM billing writer')
    monkeypatch.setattr(subscription_access,'enforce_subscription_mutation_access',forbidden)
    monkeypatch.setattr(subscription_access,'enforce_business_access',forbidden)
    models=(User,Client,Business,VisitorAccount,VisitorSession,NotificationOutbox)
    before=[db.query(model).count() for model in models]
    sql=[]
    def record(_connection,_cursor,statement,*_args):
        sql.append(statement)
    event.listen(db.get_bind(),'before_cursor_execute',record)
    try:
        result=run(db,world)
        db.commit()
    finally:
        event.remove(db.get_bind(),'before_cursor_execute',record)
    receipt=db.get(VisitorClaim,world['claim'])
    grant=db.get(VisitorGrant,result.grant_id)
    assert (grant.account_id,grant.business_id,grant.client_id,grant.scope,grant.visit_id)==(
        world['one'],world['business'],world['client'],'client',None)
    assert receipt.consumed_at==NOW and receipt.consumed_account_id==world['one']
    assert receipt.grant_id==grant.id and result.created is True
    assert [db.query(model).count() for model in models]==before
    assert world['raw'] not in repr(result) and world['session'] not in repr(result)
    assert not any('WHERE visitor_accounts.email_canonical =' in statement for statement in sql)


@pytest.mark.parametrize('raw',[None,'','vch_'+'A'*43,'vst_'+'A'*43,'vci_'+'A'*42+'B','vci_'+'A'*44,'vci_'+'A'*43,'\ud800',True])
def test_only_known_canonical_invitation_namespace_is_accepted(db,world,raw):
    with pytest.raises(accept.InvalidClientInvite):
        accept.accept_client_invite(db,world['session'],raw)
    db.rollback();assert_pending(db,world)


@pytest.mark.parametrize('raw',[None,'','crm-token','vch_'+'A'*43,'vst_'+'A'*43])
def test_only_current_visitor_session_authenticates(db,world,raw):
    with pytest.raises(accept.InvalidVisitorSession):
        accept.accept_client_invite(db,raw,world['raw'])
    db.rollback();assert_pending(db,world)


@pytest.mark.parametrize('change',['expired','revoked','recipient','issuer_null','wrong_kind','business_deleted','client_deleted','membership_removed'])
def test_first_accept_fails_closed_for_unavailable_proof_or_target(db,world,change):
    receipt=db.get(VisitorClaim,world['claim'])
    error=accept.InvalidClientInvite
    if change=='expired':world['clock'][0]=NOW+timedelta(minutes=30)
    elif change=='revoked':receipt.revoked_at=NOW
    elif change=='recipient':receipt.recipient_email='someone.else@example.com'
    elif change=='issuer_null':
        receipt.issued_by_user_id=None
        error=accept.ClientInviteAuthorityDenied
    elif change=='wrong_kind':
        receipt.kind='manage_visit';receipt.visit_id=world['visit'];receipt.recipient_email=None
        receipt.issued_by_user_id=None;receipt.consumed_at=NOW
    elif change=='business_deleted':
        db.get(Business,world['business']).deleted_at=NOW;error=accept.ClientInviteAuthorityDenied
    elif change=='client_deleted':db.get(Client,world['client']).deleted_at=NOW
    else:
        db.execute(delete(ClientBusinessRelation).where(ClientBusinessRelation.business_id==world['business']))
        error=accept.ClientInviteAuthorityDenied
    db.commit()
    with pytest.raises(error):run(db,world)
    db.rollback()
    assert db.query(VisitorGrant).count()==0


@pytest.mark.parametrize('scope',['client','visit'])
def test_any_foreign_grant_to_fixed_business_client_prevents_first_accept(db,world,scope):
    db.add(VisitorGrant(account_id=world['two'],business_id=world['business'],client_id=world['client'],
        scope=scope,visit_id=world['visit'] if scope=='visit' else None))
    db.commit()
    with pytest.raises(accept.ClientInviteConflict):run(db,world)
    db.rollback()
    assert db.query(VisitorGrant).one().account_id==world['two']
    assert db.get(VisitorClaim,world['claim']).consumed_at is None


def test_own_client_grant_is_reused_and_own_visit_grant_remains(db,world):
    existing=VisitorGrant(account_id=world['one'],business_id=world['business'],client_id=world['client'],scope='client')
    single=VisitorGrant(account_id=world['one'],business_id=world['business'],client_id=world['client'],scope='visit',visit_id=world['visit'])
    db.add_all([existing,single]);db.commit()
    result=run(db,world);db.commit()
    assert result.grant_id==existing.id and result.created is False
    assert db.query(VisitorGrant).count()==2 and db.get(VisitorGrant,single.id).revoked_at is None


def test_own_spent_replay_ignores_old_email_ttl_issuer_and_membership(db,world,monkeypatch):
    first=run(db,world);db.commit()
    world['clock'][0]=NOW+timedelta(days=1)
    db.get(VisitorAccount,world['one']).email_canonical='new.verified@example.com'
    db.get(User,world['staff']).deleted_at=NOW
    db.execute(delete(ClientBusinessRelation))
    db.commit()
    monkeypatch.setattr(accept.authority,'authorize_client_invite',lambda *_a,**_k:pytest.fail('Spent receipt called issuer guard'))
    repeated=run(db,world);db.commit()
    assert repeated.grant_id==first.grant_id and repeated.created is False
    assert db.query(VisitorGrant).count()==1


@pytest.mark.parametrize('change',['owner_null','grant_null','revoked_grant','replaced_grant','revoked_receipt'])
def test_spent_receipt_never_revives_tombstone_or_switches_grants(db,world,change):
    first=run(db,world);db.commit()
    receipt=db.get(VisitorClaim,world['claim'])
    if change=='owner_null':receipt.consumed_account_id=None
    elif change=='grant_null':db.execute(delete(VisitorGrant).where(VisitorGrant.id==first.grant_id))
    elif change=='revoked_receipt':receipt.revoked_at=NOW
    else:
        db.get(VisitorGrant,first.grant_id).revoked_at=NOW;db.flush()
        if change=='replaced_grant':
            db.add(VisitorGrant(account_id=world['one'],business_id=world['business'],client_id=world['client'],scope='client'))
    db.commit()
    with pytest.raises(accept.InvalidClientInvite):run(db,world)
    db.rollback()
    assert db.get(VisitorClaim,world['claim']).consumed_at==NOW


def test_foreign_account_cannot_replay_spent_proof(db,world):
    first=run(db,world);db.commit()
    account=visitor_sessions.lock_verified_account(db,world['two'])
    _,raw=visitor_sessions.create_session(db,account);db.commit()
    with pytest.raises(accept.ClientInviteConflict):run(db,world,session=raw)
    db.rollback()
    assert db.get(VisitorGrant,first.grant_id).account_id==world['one']


def test_actual_deferred_commit_failure_rolls_back_claim_and_grant(db,world):
    run(db,world)
    db.execute(text('CREATE TEMP TABLE invite_deferred_failure (value int UNIQUE DEFERRABLE INITIALLY DEFERRED)'))
    db.execute(text('INSERT INTO invite_deferred_failure VALUES (1),(1)'))
    with pytest.raises(IntegrityError):db.commit()
    db.rollback();assert_pending(db,world)
    run(db,world);db.commit()
    assert db.query(VisitorGrant).count()==1


@contextmanager
def real_wait(engine,matching):
    reached,pid=Event(),[]
    def capture(conn,cursor,statement,parameters,context,executemany):
        if matching(statement):
            pid[:]=[conn.connection.driver_connection.get_backend_pid()];reached.set()
    event.listen(engine,'before_cursor_execute',capture)
    try:
        def blocked(holder_pid):
            assert reached.wait(5),'worker missed expected SQL'
            until=time.monotonic()+5
            with engine.connect() as observer:
                while time.monotonic()<until:
                    if holder_pid in observer.execute(text('SELECT pg_blocking_pids(:pid)'),{'pid':pid[0]}).scalar_one():return
                    time.sleep(.01)
            pytest.fail('expected real PostgreSQL lock wait')
        yield blocked
    finally:event.remove(engine,'before_cursor_execute',capture)


def attempt(engine,world,raw=None,session=None):
    with Session(engine) as worker:
        try:
            result=run(worker,world,raw,session);worker.commit()
            return 'success',result.grant_id
        except (accept.InvalidClientInvite,accept.InvalidVisitorSession,accept.ClientInviteConflict,
                accept.ClientInviteAuthorityDenied,accept.ClientInviteBusy) as error:
            worker.rollback();return type(error).__name__,None


def test_pending_hint_becoming_spent_while_account_wait_skips_expired_issuer(db,world):
    engine=db.get_bind()
    with Session(engine) as holder:
        first=run(holder,world)
        holder_pid=holder.execute(text('SELECT pg_backend_pid()')).scalar_one()
        with real_wait(engine,lambda s:'FROM visitor_accounts' in s and 'FOR UPDATE' in s) as blocked:
            with ThreadPoolExecutor(max_workers=1) as pool:
                future=pool.submit(attempt,engine,world)
                try:
                    blocked(holder_pid)
                    holder.get(User,world['staff']).deleted_at=NOW
                    holder.commit()
                    assert future.result(timeout=10)==('success',first.grant_id)
                finally:holder.rollback()
    assert db.query(VisitorGrant).count()==1


@pytest.mark.parametrize('change',['proof_ttl','session_ttl','recipient','session_replaced'])
def test_after_account_wait_rechecks_fresh_time_email_and_exact_session(db,world,change):
    engine=db.get_bind()
    with Session(engine) as holder:
        account=visitor_sessions.lock_verified_account(holder,world['one'])
        holder_pid=holder.execute(text('SELECT pg_backend_pid()')).scalar_one()
        with real_wait(engine,lambda s:'FROM visitor_accounts' in s and 'FOR UPDATE' in s) as blocked:
            with ThreadPoolExecutor(max_workers=1) as pool:
                future=pool.submit(attempt,engine,world)
                try:
                    blocked(holder_pid)
                    if change=='recipient':account.email_canonical='changed@example.com'
                    elif change=='session_replaced':
                        old=holder.query(VisitorSession).filter_by(token_hash=hashlib.sha256(world['session'].encode()).hexdigest()).one()
                        digest=old.token_hash;old.token_hash='f'*64;holder.flush()
                        holder.add(VisitorSession(account_id=world['one'],token_hash=digest,created_at=NOW,expires_at=NOW+timedelta(days=30)))
                    else:world['clock'][0]=NOW+timedelta(days=31) if change=='session_ttl' else NOW+timedelta(minutes=30)
                    holder.commit()
                    expected='InvalidVisitorSession' if change in {'session_ttl','session_replaced'} else 'InvalidClientInvite'
                    assert future.result(timeout=10)==(expected,None)
                finally:holder.rollback()
    assert_pending(db,world)


def test_permission_narrowing_committed_during_client_wait_is_not_cached(db,world):
    engine=db.get_bind()
    with Session(engine) as holder:
        holder.query(Client.id).filter_by(id=world['client']).with_for_update(key_share=True).one()
        holder_pid=holder.execute(text('SELECT pg_backend_pid()')).scalar_one()
        with real_wait(engine,lambda s:'FROM clients' in s and 'FOR NO KEY UPDATE' in s) as blocked:
            with ThreadPoolExecutor(max_workers=1) as pool:
                future=pool.submit(attempt,engine,world)
                try:
                    blocked(holder_pid)
                    holder.execute(update(User).where(User.id==world['staff']).values(role='client'))
                    holder.commit()
                    assert future.result(timeout=10)==('ClientInviteAuthorityDenied',None)
                finally:holder.rollback()
    assert_pending(db,world)


def test_user_first_writer_causes_nowait_retry_and_client_lock_is_released(db,world):
    engine=db.get_bind()
    with Session(engine) as holder:
        holder.query(User.id).filter_by(id=world['staff']).with_for_update().one()
        with ThreadPoolExecutor(max_workers=1) as pool:
            outcome=pool.submit(attempt,engine,world).result(timeout=5)
        assert outcome==('ClientInviteBusy',None)
        holder.query(Client.id).filter_by(id=world['client']).with_for_update(nowait=True,key_share=True).one()
        holder.rollback()
    assert_pending(db,world)


def test_unique_grant_wait_that_rolls_back_rechecks_invitation_ttl(db,world):
    engine=db.get_bind()
    with Session(engine) as holder:
        holder.add(VisitorGrant(account_id=world['two'],business_id=world['business'],client_id=world['client'],scope='client'))
        holder.flush();holder_pid=holder.execute(text('SELECT pg_backend_pid()')).scalar_one()
        with real_wait(engine,lambda s:s.startswith('INSERT INTO visitor_grants')) as blocked:
            with ThreadPoolExecutor(max_workers=1) as pool:
                future=pool.submit(attempt,engine,world)
                try:
                    blocked(holder_pid)
                    world['clock'][0]=NOW+timedelta(minutes=30)
                    holder.rollback()
                    assert future.result(timeout=10)==('InvalidClientInvite',None)
                finally:holder.rollback()
    assert_pending(db,world)


def test_two_recipient_proofs_for_same_client_have_one_winner(db,world):
    second=visitor_sessions.lock_verified_account(db,world['two'])
    _,session=visitor_sessions.create_session(db,second)
    raw='vci_'+secrets.token_urlsafe(32)
    receipt=VisitorClaim(kind='client_invite',proof_hash=hashlib.sha256(raw.encode()).hexdigest(),
        business_id=world['business'],client_id=world['client'],recipient_email=second.email_canonical,
        issued_by_user_id=world['staff'],created_at=NOW,expires_at=NOW+timedelta(minutes=30))
    db.add(receipt);db.commit()
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=[future.result(timeout=10) for future in [pool.submit(attempt,db.get_bind(),world),
            pool.submit(attempt,db.get_bind(),world,raw,session)]]
    assert sorted(item[0] for item in results)==['ClientInviteConflict','success']
    assert db.query(VisitorGrant).count()==1
    assert db.query(VisitorClaim).filter(VisitorClaim.consumed_at.is_not(None)).count()==1


def test_stale_issuer_identity_map_does_not_keep_old_edit_rights(db,world):
    cached=db.get(User,world['staff'])
    assert cached.role=='specialist'
    with Session(db.get_bind()) as writer:
        writer.execute(update(User).where(User.id==world['staff']).values(role='client'))
        writer.commit()
    assert cached.role=='specialist'
    with pytest.raises(accept.ClientInviteAuthorityDenied):run(db,world)
    db.rollback();assert_pending(db,world)


def test_expiry_after_actual_final_timestamp_update_rolls_back_everything(db,world):
    updates=[]
    def advance(_conn,_cursor,statement,*_args):
        if statement.startswith('UPDATE visitor_claims'):
            updates.append(statement)
            world['clock'][0]=NOW+timedelta(seconds=1) if len(updates)==1 else NOW+timedelta(minutes=30)
    event.listen(db.get_bind(),'after_cursor_execute',advance)
    try:
        with pytest.raises(accept.InvalidClientInvite):run(db,world)
        assert len(updates)==2
    finally:event.remove(db.get_bind(),'after_cursor_execute',advance)
    db.rollback();assert_pending(db,world)


def test_concurrent_manage_visit_and_client_invite_cannot_split_ownership(db,world,monkeypatch):
    from app.api.core import visitor_manage_claim
    from app.db.models.booking_manage_token import BookingManageToken
    monkeypatch.setattr(visitor_manage_claim,'utcnow',lambda:world['clock'][0])
    other=visitor_sessions.lock_verified_account(db,world['two'])
    _,session=visitor_sessions.create_session(db,other)
    raw_manage='independent-manage-vs-invite-proof'
    db.get(BookingManageToken,world['token']).token_hash=hashlib.sha256(raw_manage.encode()).hexdigest()
    db.commit()
    engine,barrier=db.get_bind(),Barrier(2)
    def overlap(_conn,_cursor,statement,*_args):
        if 'FROM clients' in statement and 'FOR NO KEY UPDATE' in statement:
            barrier.wait(timeout=5)
    def manage():
        with Session(engine) as worker:
            try:
                result=visitor_manage_claim.consume_guest_manage(worker,session,raw_manage)
                worker.commit();return 'success',result.grant_id
            except visitor_manage_claim.ManageClaimConflict:
                worker.rollback();return 'ManageClaimConflict',None
    event.listen(engine,'before_cursor_execute',overlap)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            one=pool.submit(attempt,engine,world)
            two=pool.submit(manage)
            results=[one.result(timeout=10),two.result(timeout=10)]
    finally:event.remove(engine,'before_cursor_execute',overlap)
    assert sum(item[0]=='success' for item in results)==1
    assert sum(item[0].endswith('Conflict') for item in results)==1
    assert db.query(VisitorGrant).count()==1
    assert len({row.account_id for row in db.query(VisitorGrant)})==1


@pytest.mark.parametrize('change',['ttl','recipient','revoked'])
def test_claim_lock_wait_rechecks_fresh_proof_state_after_initial_authority(db,world,change):
    engine=db.get_bind()
    with Session(engine) as holder:
        receipt=holder.query(VisitorClaim).filter_by(id=world['claim']).with_for_update().one()
        holder_pid=holder.execute(text('SELECT pg_backend_pid()')).scalar_one()
        with real_wait(engine,lambda s:'FROM visitor_claims' in s and 'FOR UPDATE' in s) as blocked:
            with ThreadPoolExecutor(max_workers=1) as pool:
                future=pool.submit(attempt,engine,world)
                try:
                    blocked(holder_pid)
                    if change=='ttl':world['clock'][0]=NOW+timedelta(minutes=30)
                    elif change=='recipient':receipt.recipient_email='fresh.recipient@example.com'
                    else:receipt.revoked_at=NOW
                    holder.commit()
                    assert future.result(timeout=10)==('InvalidClientInvite',None)
                finally:holder.rollback()
    assert db.query(VisitorGrant).count()==0
    assert db.get(VisitorClaim,world['claim']).consumed_at is None
