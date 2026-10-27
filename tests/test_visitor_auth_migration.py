"""Credential migrations cannot modify existing visitor identity or CRM data."""
import json

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
import pytest
from sqlalchemy import MetaData, inspect, select, text
from sqlalchemy.exc import ProgrammingError

from app.api.core.clock import utcnow
from app.db.models.visitor_account import GOOGLE_ISSUER, VisitorAccount, VisitorGoogleIdentity
from app.db.models.visitor_auth import VisitorChallenge, VisitorSession

NEW_TABLES = {"visitor_challenges", "visitor_sessions"}


def before_auth_rows(bind):
    metadata = MetaData()
    metadata.reflect(bind=bind)
    with bind.connect() as connection:
        return {table.name: sorted(json.dumps(dict(row), sort_keys=True, default=str)
                for row in connection.execute(select(*[column for column in table.c
                    if not (table.name == "visits" and column.name == "currency_snapshot")])).mappings())
                for table in metadata.sorted_tables if table.name not in NEW_TABLES | {"visitor_grants", "visitor_claims", "alembic_version"}}


def seed_identity(db, make_user):
    make_user(email="shared-address@example.com")
    visitor = VisitorAccount(email_canonical="shared-address@example.com", email_verified_at=utcnow())
    db.add(visitor)
    db.flush()
    db.add(VisitorGoogleIdentity(account_id=visitor.id, issuer=GOOGLE_ISSUER, subject="unchanged-subject"))
    db.commit()
    visitor_id = visitor.id
    db.rollback()
    return visitor_id


def test_creating_auth_records_does_not_create_or_change_a_crm_or_account(db, make_user):
    owner_id = seed_identity(db, make_user)
    bind = db.get_bind()
    before = before_auth_rows(bind)
    from datetime import timedelta
    now = utcnow()
    db.add(VisitorChallenge(purpose="reset", account_id=owner_id, email_canonical="shared-address@example.com",
        token_hash="a" * 64, created_at=now, expires_at=now + timedelta(minutes=30)))
    db.add(VisitorSession(account_id=owner_id, token_hash="b" * 64, created_at=now,
        expires_at=now + timedelta(days=30)))
    db.commit()
    db.rollback()
    assert before_auth_rows(bind) == before


def test_0079_0080_roundtrip_has_one_head_and_keeps_all_previous_rows(db, make_user):
    seed_identity(db, make_user)
    bind = db.get_bind()
    before = before_auth_rows(bind)
    config = Config("alembic.ini")
    assert ScriptDirectory.from_config(config).get_heads() == ["0082_visit_currency_snapshot"]
    try:
        command.downgrade(config, "0079_visitor_accounts")
        assert not (NEW_TABLES & set(inspect(bind).get_table_names()))
        assert before_auth_rows(bind) == before
        command.upgrade(config, "0080_visitor_auth_credentials")
        assert NEW_TABLES <= set(inspect(bind).get_table_names())
        assert before_auth_rows(bind) == before
        command.downgrade(config, "0079_visitor_accounts")
        assert before_auth_rows(bind) == before
    finally:
        db.rollback()
        command.upgrade(config, "head")
    assert before_auth_rows(bind) == before


def test_failed_upgrade_rolls_back_created_tables_and_keeps_the_old_revision(db, make_user):
    seed_identity(db, make_user)
    bind = db.get_bind()
    before = before_auth_rows(bind)
    config = Config("alembic.ini")
    try:
        command.downgrade(config, "0079_visitor_accounts")
        # A deliberate local-only collision occurs after challenge DDL has run.
        with bind.begin() as connection:
            connection.execute(text("CREATE TABLE visitor_sessions (sentinel integer NOT NULL)"))
            connection.execute(text("INSERT INTO visitor_sessions VALUES (42)"))
        with pytest.raises(ProgrammingError):
            command.upgrade(config, "0080_visitor_auth_credentials")
        assert not inspect(bind).has_table("visitor_challenges")
        with bind.connect() as connection:
            assert connection.execute(text("SELECT sentinel FROM visitor_sessions")).scalars().all() == [42]
            assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar() == "0079_visitor_accounts"
        assert before_auth_rows(bind) == before
    finally:
        db.rollback()
        with bind.begin() as connection:
            connection.execute(text("DROP TABLE IF EXISTS visitor_sessions"))
        command.upgrade(config, "head")
    assert before_auth_rows(bind) == before
