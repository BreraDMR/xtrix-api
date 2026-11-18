"""CPA-19: the visitor proof-mail queue (0091) rolls back and forward without touching older rows.

test_visitor_proof_mail.py already checks the guard against a pending job. Here we check
the other half: a queue with only finished jobs can be removed and recreated, and a job
that's mid-send blocks the downgrade the same way a pending one does.
"""
from datetime import timedelta

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import DBAPIError

from app.db.models.visitor_proof_mail_job import VisitorProofMailJob as Job
from tests.test_visitor_access_migration import previous_rows, seed_prior_credentials
from tests.test_visitor_access_models import NOW, seed_access_world

BEFORE_QUEUE = "0090_visit_price_corrections"
CONSTRAINTS = {"ck_visitor_proof_mail_purpose", "ck_visitor_proof_mail_status", "ck_visitor_proof_mail_attempts",
               "ck_visitor_proof_mail_secret", "ck_visitor_proof_mail_schedule"}


def _job(status, **extra):
    return Job(purpose="register", locale="cs", origin="https://visitor.example", status=status, attempts=1,
               deadline=NOW + timedelta(hours=1), created_at=NOW, updated_at=NOW, **extra)


def test_finished_queue_roundtrip_keeps_visitor_rows_and_restores_constraints(db):
    seed_prior_credentials(db, seed_access_world(db))
    db.add_all([_job("sent"), _job("expired"), _job("cancelled")])
    db.commit()
    bind, config = db.get_bind(), Config("alembic.ini")
    before = previous_rows(bind)
    try:
        command.downgrade(config, BEFORE_QUEUE)
        assert not inspect(bind).has_table("visitor_proof_mail_jobs")
        assert previous_rows(bind) == before
        command.upgrade(config, "head")
        checks = {check["name"] for check in inspect(bind).get_check_constraints("visitor_proof_mail_jobs")}
        assert CONSTRAINTS <= checks
        assert "ix_visitor_proof_mail_due" in {index["name"] for index in inspect(bind).get_indexes("visitor_proof_mail_jobs")}
        with bind.connect() as connection:
            # finished jobs carry no secrets, so dropping them loses nothing a visitor needs
            assert connection.execute(text("SELECT count(*) FROM visitor_proof_mail_jobs")).scalar_one() == 0
        assert previous_rows(bind) == before
    finally:
        db.rollback()
        command.upgrade(config, "head")


def test_job_in_flight_blocks_the_downgrade_like_a_pending_one(db):
    db.add(_job("sending", key_id="k1", nonce=b"n" * 12, ciphertext=b"c" * 16,
                lease_until=NOW + timedelta(minutes=1), lease_ticket="t" * 32))
    db.commit()
    bind, config = db.get_bind(), Config("alembic.ini")
    db.close()
    try:
        with pytest.raises(DBAPIError, match="still queued"):
            command.downgrade(config, BEFORE_QUEUE)
        with bind.connect() as connection:
            assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == "0093_business_specialist_type"
            assert connection.execute(text("SELECT count(*) FROM visitor_proof_mail_jobs")).scalar_one() == 1
    finally:
        db.rollback()
        command.upgrade(config, "head")
