"""Live visitor proof mail: stored encrypted with the challenge, sent after commit, retried by the worker."""
from base64 import urlsafe_b64encode
from datetime import timedelta
import importlib.util
from pathlib import Path

import pytest
from pydantic import SecretStr
from sqlalchemy import select, text, update
from sqlalchemy.orm import Session

from app.api.core import notification_transport, visitor_proof_mail as proof_mail
from app.api.core.config import settings
from app.api.core.notification_transport import DeliveryResult
from app.db.models.visitor_auth import VisitorChallenge
from app.db.models.visitor_proof_mail_job import VisitorProofMailJob as Job

KEY_A = 'k1:' + urlsafe_b64encode(b'A' * 32).decode().rstrip('=')
KEY_B = 'k2:' + urlsafe_b64encode(b'B' * 32).decode().rstrip('=')
PASSWORD = 'Safe!Visitor-57-Pass'


class FakeTransport:
    name = 'fake'

    def __init__(self, status='sent'):
        self.status = status
        self.sent = []

    def send(self, message):
        self.sent.append(message)
        if self.status == 'raise':
            raise ConnectionError('provider down')
        return DeliveryResult(self.status, self.name, error=None if self.status == 'sent' else 'smtp_temporary')


@pytest.fixture
def live(monkeypatch):
    monkeypatch.setattr(settings, 'visitor_auth_mail_mode', 'live')
    monkeypatch.setattr(settings, 'environment', 'production')
    monkeypatch.setattr(settings, 'frontend_url', 'https://visitor.example')
    monkeypatch.setattr(settings, 'visitor_proof_mail_keys', SecretStr(KEY_A))
    transport = FakeTransport()
    monkeypatch.setattr(notification_transport, 'get_notification_transport', lambda: transport)
    return transport


def token(message):
    return message.text.split('#token=', 1)[1].splitlines()[0]


def jobs(db):
    with Session(db.get_bind()) as fresh:
        return fresh.execute(select(Job).order_by(Job.id)).scalars().all()


def register(client, email='new.visitor@example.com', locale='ru'):
    r = client.post('/visitor/auth/register/request', json={'email': email}, headers={'Accept-Language': locale})
    assert r.status_code == 202, r.text
    return r


def test_registration_link_is_sent_once_and_nothing_secret_stays_in_the_database(client, db, live):
    register(client)
    [message] = live.sent
    assert message.recipient == 'new.visitor@example.com'
    assert message.text.startswith('Чтобы завершить регистрацию')
    raw = token(message)
    [job] = jobs(db)
    assert job.status == 'sent' and job.attempts == 1
    assert job.ciphertext is None and job.nonce is None and job.key_id is None
    # the raw link never appears anywhere in the database
    dump = db.execute(text("SELECT string_agg(t::text, '|') FROM visitor_proof_mail_jobs t")).scalar()
    assert raw not in dump
    completed = client.post('/visitor/auth/register/complete', json={'token': raw, 'new_password': PASSWORD})
    assert completed.status_code == 201, completed.text


def test_failed_immediate_send_is_retried_by_the_worker_with_the_same_link(client, db, live, monkeypatch):
    live.status = 'raise'
    register(client)
    [job] = jobs(db)
    assert job.status == 'pending' and job.attempts == 1 and job.last_error == 'transport_ConnectionError'
    assert job.ciphertext is not None and b'vch_' not in job.ciphertext
    first_link = token(live.sent[0])
    live.status = 'sent'
    db.execute(update(Job).values(next_attempt_at=Job.created_at))
    db.commit()
    assert proof_mail.run_scheduled()['handled'] == 1
    assert token(live.sent[-1]) == first_link
    assert jobs(db)[0].status == 'sent'
    assert proof_mail.run_scheduled()['handled'] == 0


def test_a_crash_before_sending_leaves_the_job_for_the_worker(client, db, live, monkeypatch):
    monkeypatch.setattr(proof_mail, 'deliver_after_commit', lambda *_args: None)
    register(client)
    assert live.sent == [] and jobs(db)[0].status == 'pending'
    assert proof_mail.run_scheduled()['handled'] == 1
    assert len(live.sent) == 1 and jobs(db)[0].status == 'sent'


def test_a_lost_lease_is_reclaimed_but_the_link_stays_the_same(client, db, live, monkeypatch):
    monkeypatch.setattr(proof_mail, 'deliver_after_commit', lambda *_args: None)
    register(client)
    claimed = proof_mail._claim(db)
    assert claimed is not None and jobs(db)[0].status == 'sending'
    # worker died mid-send; the lease runs out
    db.execute(update(Job).values(lease_until=Job.created_at - timedelta(minutes=1)))
    db.commit()
    assert proof_mail.run_scheduled()['handled'] == 1
    assert jobs(db)[0].status == 'sent' and jobs(db)[0].attempts == 2
    # the stale worker can't overwrite the finished job
    proof_mail._finish(db, claimed[0], claimed[1], 'failed', 'stale')
    assert jobs(db)[0].status == 'sent'


def test_used_or_expired_proofs_are_dropped_without_sending(client, db, live, monkeypatch):
    monkeypatch.setattr(proof_mail, 'deliver_after_commit', lambda *_args: None)
    register(client, 'used@example.com')
    register(client, 'late@example.com')
    first, second = jobs(db)
    db.execute(update(VisitorChallenge).where(VisitorChallenge.id == first.challenge_id).values(
        consumed_at=VisitorChallenge.created_at))
    db.execute(update(VisitorChallenge).where(VisitorChallenge.id == second.challenge_id).values(
        expires_at=VisitorChallenge.created_at + timedelta(microseconds=1)))
    db.commit()
    proof_mail.run_scheduled()
    assert live.sent == []
    assert [(job.status, job.ciphertext) for job in jobs(db)] == [('cancelled', None), ('expired', None)]


def test_deleted_challenge_cancels_the_job(client, db, live, monkeypatch):
    monkeypatch.setattr(proof_mail, 'deliver_after_commit', lambda *_args: None)
    register(client)
    db.execute(text('DELETE FROM visitor_challenges'))
    db.commit()
    assert jobs(db)[0].challenge_id is None
    proof_mail.run_scheduled()
    assert live.sent == [] and jobs(db)[0].status == 'cancelled'


def test_retries_stop_after_five_attempts(client, db, live, monkeypatch):
    live.status = 'failed'
    register(client)
    for _ in range(6):
        db.execute(update(Job).where(Job.status == 'pending').values(next_attempt_at=Job.created_at))
        db.commit()
        proof_mail.run_scheduled()
    [job] = jobs(db)
    assert job.status == 'failed' and job.attempts == 5 and job.ciphertext is None
    assert len(live.sent) == 5


def test_rotated_key_still_decrypts_old_jobs_and_a_missing_key_fails_closed(client, db, live, monkeypatch):
    monkeypatch.setattr(proof_mail, 'deliver_after_commit', lambda *_args: None)
    register(client, 'one@example.com')
    monkeypatch.setattr(settings, 'visitor_proof_mail_keys', SecretStr(KEY_B + ',' + KEY_A))
    register(client, 'two@example.com')
    assert [job.key_id for job in jobs(db)] == ['k1', 'k2']
    proof_mail.run_scheduled()
    assert sorted(m.recipient for m in live.sent) == ['one@example.com', 'two@example.com']
    register(client, 'three@example.com')
    monkeypatch.setattr(settings, 'visitor_proof_mail_keys', SecretStr(KEY_A))
    proof_mail.run_scheduled()
    assert jobs(db)[-1].status == 'failed' and jobs(db)[-1].last_error == 'key_unavailable'


def test_ciphertext_moved_to_another_job_does_not_decrypt(client, db, live, monkeypatch):
    monkeypatch.setattr(proof_mail, 'deliver_after_commit', lambda *_args: None)
    register(client, 'one@example.com')
    register(client, 'two@example.com')
    first, second = jobs(db)
    db.execute(update(Job).where(Job.id == second.id).values(nonce=first.nonce, ciphertext=first.ciphertext))
    db.commit()
    proof_mail.run_scheduled()
    assert [m.recipient for m in live.sent] == ['one@example.com']
    assert jobs(db)[1].status == 'failed' and jobs(db)[1].last_error == 'decrypt_failed'


@pytest.mark.parametrize('keys', ['', 'k1:short', 'no-colon', 'k1:' + urlsafe_b64encode(b'x' * 16).decode()])
def test_live_mode_without_a_usable_key_refuses_before_writing(client, db, live, monkeypatch, keys):
    monkeypatch.setattr(settings, 'visitor_proof_mail_keys', SecretStr(keys))
    r = client.post('/visitor/auth/register/request', json={'email': 'x@example.com'})
    assert r.status_code == 503, r.text
    assert db.query(VisitorChallenge).count() == 0 and jobs(db) == []
    assert live.sent == []


def test_reset_for_unknown_address_is_generic_and_queues_nothing(client, db, live):
    r = client.post('/visitor/auth/password/reset/request', json={'email': 'nobody@example.com'})
    assert r.status_code == 202, r.text
    assert jobs(db) == [] and live.sent == []


def test_reset_link_reaches_a_registered_visitor_and_works(client, db, live):
    register(client, 'member@example.com', 'en')
    raw = token(live.sent[0])
    assert client.post('/visitor/auth/register/complete', json={'token': raw, 'new_password': PASSWORD}).status_code == 201
    r = client.post('/visitor/auth/password/reset/request', json={'email': 'member@example.com'})
    assert r.status_code == 202, r.text
    reset = live.sent[-1]
    assert reset.recipient == 'member@example.com' and '/visitor/reset-password#token=' in reset.text
    confirm = client.post('/visitor/auth/password/reset/confirm',
                          json={'token': token(reset), 'new_password': 'Other!Visitor-58-Pass'})
    assert confirm.status_code == 204, confirm.text


def test_worker_job_and_migration_guard(client, db, live, monkeypatch):
    spec = importlib.util.spec_from_file_location('run_jobs', Path(__file__).resolve().parents[1] / 'scripts' / 'run_jobs.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert 'visitor_mail' in module.FREQUENT
    workflow = (Path(__file__).resolve().parents[1] / '.github' / 'workflows' / 'jobs.yml').read_text()
    assert 'visitor_mail released_slots reminders followups' in workflow

    from alembic import command
    from alembic.config import Config
    from sqlalchemy.exc import DBAPIError
    monkeypatch.setattr(proof_mail, 'deliver_after_commit', lambda *_args: None)
    register(client)
    db.close()
    with pytest.raises(DBAPIError, match='still queued'):
        command.downgrade(Config('alembic.ini'), '0090_visit_price_corrections')
    assert db.execute(text('SELECT version_num FROM alembic_version')).scalar_one() == '0091_visitor_proof_mail_jobs'
    db.rollback()
