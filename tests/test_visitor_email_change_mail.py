"""Only local committed email-change proofs enter a separate bounded capture."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta
from threading import Event
from urllib.parse import parse_qs, urlsplit

import pytest
from sqlalchemy import delete, event, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.core import visitor_email_change_mail as mail
from app.api.core.config import settings
from app.api.core import visitor_challenges, visitor_email_change, visitor_recent_auth, visitor_sessions
from app.api.core import visitor_registration_mail, visitor_reset_mail
from app.db.models.visitor_auth import VisitorChallenge, VisitorSession
from app.db.models.visitor_account import VisitorAccount, VisitorGoogleIdentity
from app.db.models.visitor_access import VisitorGrant
from app.db.models.user import User
from app.db.models.client import Client
from app.db.models.business import Business
from app.db.models.notification_outbox import NotificationOutbox
from app.db.models.subscription import Subscription
from tests import test_visitor_email_change as email_fixtures

world=email_fixtures.world


@pytest.fixture
def mail_world(world,monkeypatch):
    monkeypatch.setattr(settings,'visitor_auth_mail_mode','capture')
    monkeypatch.setattr(settings,'environment','test')
    monkeypatch.setattr(settings,'frontend_url','https://visitor.example')
    monkeypatch.setattr(mail,'utcnow',lambda:world['clock'][0])
    mail.clear_capture()
    yield world
    mail.clear_capture()


def test_only_committed_proof_is_captured_for_the_new_address(db,mail_world):
    world=mail_world
    prepared=mail.prepare(db,world['sessions'][0],'New.Contact+Tag@EXAMPLE.COM','ru')
    assert prepared.recipient=='new.contact+tag@example.com'
    assert prepared.recipient!=world['old_email']
    assert repr(prepared)=='PreparedEmailChangeMail()'
    assert 'https://visitor.example/visitor/email-change#token=vch_' in prepared.text
    with pytest.raises(mail.EmailChangeMailUnavailable):mail.capture_after_commit(db,prepared)
    db.commit()
    mail.capture_after_commit(db,prepared)
    assert mail.captured_messages()==(prepared,)


def raw_proof(prepared):
    link=next(line for line in prepared.text.splitlines() if line.startswith(('http://','https://')))
    return parse_qs(urlsplit(link).fragment)['token'][0]


def committed(db,world,email='new.contact@example.com',locale='en'):
    prepared=mail.prepare(db,world['sessions'][0],email,locale)
    db.commit()
    return prepared


@pytest.mark.parametrize('mode,environment',[('disabled','test'),('disabled','development'),('capture','production'),('capture','staging')])
def test_unavailable_gate_rejects_before_authentication_or_proof_issue(db,mail_world,monkeypatch,mode,environment):
    monkeypatch.setattr(settings,'visitor_auth_mail_mode',mode)
    monkeypatch.setattr(settings,'environment',environment)
    monkeypatch.setattr(visitor_recent_auth,'lock_recent_actor',lambda *_a,**_k:pytest.fail('Unavailable gate reached auth'))
    monkeypatch.setattr(visitor_email_change,'issue',lambda *_a,**_k:pytest.fail('Unavailable gate issued proof'))
    with pytest.raises(mail.EmailChangeMailUnavailable):mail.prepare(db,mail_world['sessions'][0],'new@example.com','en')
    assert db.query(VisitorChallenge).count()==0 and mail.captured_messages()==()


@pytest.mark.parametrize('url',[None,'','javascript:alert(1)','https://user:secret@example.com','https://visitor.example/path',
    'https://visitor.example/?next=elsewhere','https://visitor.example/#token=bad','https://visitor.example:bad','https://visitor.example bad'])
def test_capture_link_requires_configured_bare_origin_before_issue(db,mail_world,monkeypatch,url):
    monkeypatch.setattr(settings,'frontend_url',url)
    with pytest.raises(mail.EmailChangeMailUnavailable):mail.prepare(db,mail_world['sessions'][0],'new@example.com','en')
    assert db.query(VisitorChallenge).count()==0


@pytest.mark.parametrize('environment',['test','development'])
@pytest.mark.parametrize('locale,subject',[('en','Xtrix email change'),('ru','Смена почты Xtrix'),('cz','Změna e-mailu Xtrix'),('unknown','Xtrix email change')])
def test_local_text_uses_only_new_purpose_and_fragment_not_query(db,mail_world,monkeypatch,environment,locale,subject):
    monkeypatch.setattr(settings,'environment',environment)
    monkeypatch.setattr(settings,'frontend_url','https://visitor.example/')
    prepared=mail.prepare(db,mail_world['sessions'][0],'new.contact@example.com',locale)
    assert prepared.subject==subject
    link=next(line for line in prepared.text.splitlines() if line.startswith('https://'))
    parsed=urlsplit(link)
    assert parsed.path=='/visitor/email-change' and parsed.query==''
    assert parse_qs(parsed.fragment)=={'token':[raw_proof(prepared)]}
    assert 'reset-password' not in prepared.text and '/visitor/register' not in prepared.text
    db.rollback()


def test_separate_observer_cannot_see_uncommitted_or_rolled_back_proof(db,mail_world):
    prepared=mail.prepare(db,mail_world['sessions'][0],'new@example.com','en')
    with Session(db.get_bind()) as unrelated:
        with pytest.raises(mail.EmailChangeMailUnavailable):mail.capture_after_commit(unrelated,prepared)
    db.rollback()
    with pytest.raises(mail.EmailChangeMailUnavailable):mail.capture_after_commit(db,prepared)
    assert mail.captured_messages()==() and db.query(VisitorChallenge).count()==0


def test_observer_is_actually_read_only_and_capture_does_not_touch_other_queues(db,mail_world):
    before_reg=visitor_registration_mail.captured_messages();before_reset=visitor_reset_mail.captured_messages()
    prepared=committed(db,mail_world)
    readonly=[]
    def check(conn,_cursor,statement,*_args):
        if statement.startswith('SELECT') and 'FROM visitor_challenges' in statement:
            readonly.append(conn.exec_driver_sql('SHOW transaction_read_only').scalar_one())
    event.listen(db.get_bind(),'after_cursor_execute',check)
    try:mail.capture_after_commit(db,prepared)
    finally:event.remove(db.get_bind(),'after_cursor_execute',check)
    assert readonly==['on']
    assert visitor_registration_mail.captured_messages()==before_reg
    assert visitor_reset_mail.captured_messages()==before_reset


@pytest.mark.parametrize('tamper',['hash','account','recipient','id'])
def test_prepared_envelope_cannot_select_another_proof_account_or_recipient(db,mail_world,tamper):
    prepared=committed(db,mail_world)
    changed=replace(prepared,**{'hash':{'challenge_hash':'f'*64},'account':{'account_id':mail_world['two']},
        'recipient':{'recipient':mail_world['old_email']},'id':{'challenge_id':prepared.challenge_id+999}}[tamper])
    with pytest.raises(mail.EmailChangeMailUnavailable):mail.capture_after_commit(db,changed)
    assert mail.captured_messages()==()


@pytest.mark.parametrize('change',['purpose','recipient','account','consumed','deleted','expired'])
def test_capture_requires_current_exact_committed_proof(db,mail_world,change):
    prepared=committed(db,mail_world)
    if change=='expired':mail_world['clock'][0]=email_fixtures.NOW+timedelta(minutes=30)
    else:
        if change=='deleted':db.execute(delete(VisitorChallenge).where(VisitorChallenge.id==prepared.challenge_id))
        else:
            values={'purpose':{'purpose':'reset'},'recipient':{'email_canonical':'different@example.com'},
                    'account':{'account_id':mail_world['two']},'consumed':{'consumed_at':email_fixtures.NOW}}[change]
            db.execute(update(VisitorChallenge).where(VisitorChallenge.id==prepared.challenge_id).values(**values))
        db.commit()
    with pytest.raises(mail.EmailChangeMailUnavailable):mail.capture_after_commit(db,prepared)
    assert mail.captured_messages()==()


def test_capture_is_single_even_with_two_local_workers(db,mail_world):
    prepared=committed(db,mail_world)
    engine=db.get_bind()
    def capture():
        with Session(engine) as own:mail.capture_after_commit(own,prepared)
    with ThreadPoolExecutor(max_workers=2) as pool:list(pool.map(lambda _n:capture(),range(2)))
    assert mail.captured_messages()==(prepared,)


def test_same_account_cooldown_does_not_reset_with_a_different_target_address(db,mail_world):
    prepared=committed(db,mail_world,'first.target@example.com')
    assert mail.prepare(db,mail_world['sessions'][0],'different.target@example.com','en') is None
    db.commit()
    assert mail.captured_messages()==() and db.query(VisitorChallenge).count()==1
    db.rollback()
    mail_world['clock'][0]+=timedelta(seconds=60)
    second=mail.prepare(db,mail_world['sessions'][0],'different.target@example.com','en')
    assert second is not None and second.challenge_id!=prepared.challenge_id
    db.rollback()


def new_login(db,world,account_id=None):
    actor=visitor_sessions.lock_verified_account(db,account_id or world['one'])
    _,raw=visitor_sessions.create_session(db,actor)
    db.commit()
    return raw


def test_five_per_hour_is_account_scoped_across_reauthentication(db,mail_world):
    world=mail_world
    for number in range(5):
        world['clock'][0]=email_fixtures.NOW+timedelta(seconds=number*60)
        assert committed(db,world,f'target.{number}@example.com') is not None
    world['clock'][0]=email_fixtures.NOW+timedelta(minutes=5)
    fresh=new_login(db,world)
    assert mail.prepare(db,fresh,'another.target@example.com','en') is None
    db.commit()
    assert db.query(VisitorChallenge).count()==5
    db.rollback()
    other=new_login(db,world,world['two'])
    independent=mail.prepare(db,other,'target.0@example.com','en')
    assert independent is not None and independent.account_id==world['two']
    db.rollback()
    world['clock'][0]=email_fixtures.NOW+timedelta(hours=1,microseconds=1)
    fresh=new_login(db,world)
    assert mail.prepare(db,fresh,'window.target@example.com','en') is not None
    db.rollback()


@pytest.mark.parametrize('target',['owner.two@example.com','unknown@example.com'])
def test_address_occupancy_never_queries_another_account(db,mail_world,target):
    statements=[]
    def record(_conn,_cursor,statement,*_args):statements.append(statement)
    event.listen(db.get_bind(),'before_cursor_execute',record)
    try:prepared=mail.prepare(db,mail_world['sessions'][0],target,'en')
    finally:event.remove(db.get_bind(),'before_cursor_execute',record)
    assert prepared.recipient==target
    assert not any(statement.lstrip().startswith('SELECT') and 'WHERE visitor_accounts.email_canonical =' in statement for statement in statements)
    db.rollback()


def test_actual_deferred_commit_failure_and_rollback_never_capture_half_request(db,mail_world):
    prepared=mail.prepare(db,mail_world['sessions'][0],'new@example.com','en')
    db.execute(text('CREATE TEMP TABLE email_mail_commit_failure (n int UNIQUE DEFERRABLE INITIALLY DEFERRED)'))
    db.execute(text('INSERT INTO email_mail_commit_failure VALUES (1),(1)'))
    with pytest.raises(IntegrityError):db.commit()
    with pytest.raises(mail.EmailChangeMailUnavailable):mail.capture_after_commit(db,prepared)
    db.rollback()
    with pytest.raises(mail.EmailChangeMailUnavailable):mail.capture_after_commit(db,prepared)
    assert db.query(VisitorChallenge).count()==0 and mail.captured_messages()==()


def test_preparation_and_capture_create_only_proof_not_identity_finance_or_outbox(db,mail_world,caplog):
    models=(VisitorAccount,VisitorSession,VisitorGoogleIdentity,VisitorGrant,User,Client,Business,Subscription,NotificationOutbox)
    before=[db.query(model).count() for model in models]
    db.rollback()
    prepared=committed(db,mail_world)
    mail.capture_after_commit(db,prepared)
    assert before==[db.query(model).count() for model in models]
    assert db.get(VisitorAccount,mail_world['one']).email_canonical==mail_world['old_email']
    assert raw_proof(prepared) not in caplog.text and prepared.recipient not in caplog.text


def test_confirmation_invalidates_main_and_sibling_capture_before_they_are_sent(db,mail_world):
    first=committed(db,mail_world,'first@example.com')
    mail_world['clock'][0]+=timedelta(seconds=60)
    second=committed(db,mail_world,'second@example.com')
    visitor_email_change.confirm(db,mail_world['sessions'][0],raw_proof(first));db.commit()
    for prepared in [first,second]:
        with pytest.raises(mail.EmailChangeMailUnavailable):mail.capture_after_commit(db,prepared)
    assert mail.captured_messages()==()


def test_capture_failure_after_commit_preserves_proof_and_does_not_turn_cooldown_into_retry(db,mail_world,monkeypatch):
    prepared=committed(db,mail_world)
    class FailedQueue:
        def __iter__(self):return iter(())
        def append(self,_item):raise RuntimeError('Synthetic capture failure')
    with monkeypatch.context() as patch:
        patch.setattr(mail,'_messages',FailedQueue())
        with pytest.raises(mail.EmailChangeMailUnavailable):mail.capture_after_commit(db,prepared)
    row=db.get(VisitorChallenge,prepared.challenge_id)
    assert row.consumed_at is None and row.token_hash==prepared.challenge_hash
    db.rollback()
    assert mail.prepare(db,mail_world['sessions'][0],'another.target@example.com','en') is None
    db.commit()
    assert mail.captured_messages()==()
    mail.capture_after_commit(db,prepared)
    assert mail.captured_messages()==(prepared,)


@pytest.mark.parametrize('expired_actor', [False, True])
def test_concurrent_request_waits_for_account_then_rechecks_cooldown_and_recent_login(db, mail_world, expired_actor):
    world = mail_world
    engine = db.get_bind()
    with Session(engine, autoflush=False) as holder:
        first = mail.prepare(holder, world['sessions'][0], 'first.target@example.com', 'en')
        holder_pid = holder.execute(text('SELECT pg_backend_pid()')).scalar_one()

        def request_second():
            with Session(engine, autoflush=False) as worker:
                try:
                    prepared = mail.prepare(worker, world['sessions'][1], 'second.target@example.com', 'en')
                    worker.commit()
                    return ('throttled' if prepared is None else 'issued')
                except visitor_recent_auth.VisitorReauthenticationRequired:
                    worker.rollback()
                    return 'reauthenticate'

        with email_fixtures.real_wait(engine, lambda sql: 'FROM visitor_accounts' in sql and 'FOR UPDATE' in sql) as blocked:
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(request_second)
                try:
                    blocked(holder_pid)
                    if expired_actor:
                        world['clock'][0] += timedelta(minutes=5)
                    holder.commit()
                    assert future.result(timeout=8) == ('reauthenticate' if expired_actor else 'throttled')
                finally:
                    holder.rollback()
        mail.capture_after_commit(holder, first)
    db.expire_all()
    rows = db.query(VisitorChallenge).filter_by(purpose='email_change', account_id=world['one']).all()
    assert len(rows) == 1 and rows[0].email_canonical == 'first.target@example.com'
    assert mail.captured_messages() == (first,)
