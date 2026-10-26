"""The access migration leaves every preexisting CRM and visitor row unchanged."""
from datetime import timedelta
import json

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
import pytest
from sqlalchemy import MetaData, inspect, select, text
from sqlalchemy.exc import ProgrammingError

from app.db.models.client_portal import ClientPortalSession, ClientPortalToken
from app.db.models.visitor_account import VisitorGoogleIdentity
from app.db.models.visitor_auth import VisitorChallenge, VisitorSession
from tests.test_visitor_access_models import NOW, seed_access_world

NEW_TABLES = {"visitor_grants", "visitor_claims"}


@pytest.fixture
def access_world(db):
    return seed_access_world(db)


def previous_rows(bind):
    metadata = MetaData()
    metadata.reflect(bind=bind)
    with bind.connect() as connection:
        return {table.name: sorted(json.dumps(dict(row), sort_keys=True, default=str)
                for row in connection.execute(select(table)).mappings())
                for table in metadata.sorted_tables if table.name not in NEW_TABLES | {"alembic_version"}}


def seed_prior_credentials(db, world):
    db.add(VisitorGoogleIdentity(subject="unchanged-google-principal", account_id=world["one"]))
    db.add(VisitorSession(account_id=world["one"], token_hash="1" * 64,
        created_at=NOW, expires_at=NOW + timedelta(days=30)))
    db.add(VisitorChallenge(purpose="reset", account_id=world["one"], email_canonical="owner.one@example.com",
        token_hash="2" * 64, created_at=NOW, expires_at=NOW + timedelta(minutes=30)))
    db.add(ClientPortalToken(business_id=world["business"], client_id=world["client"],
        token_hash="3" * 64, created_at=NOW, expires_at=NOW + timedelta(minutes=30)))
    db.add(ClientPortalSession(business_id=world["business"], client_id=world["client"],
        token_hash="4" * 64, created_at=NOW, expires_at=NOW + timedelta(days=30)))
    db.commit()
    db.rollback()


def test_0080_0081_roundtrip_preserves_all_previous_rows_and_never_backfills_email_links(db, access_world):
    seed_prior_credentials(db, access_world)
    bind, config = db.get_bind(), Config("alembic.ini")
    before = previous_rows(bind)
    assert ScriptDirectory.from_config(config).get_heads() == ["0081_visitor_access"]
    try:
        for _ in range(2):
            command.downgrade(config, "0080_visitor_auth_credentials")
            assert not NEW_TABLES & set(inspect(bind).get_table_names())
            assert previous_rows(bind) == before
            command.upgrade(config, "0081_visitor_access")
            assert NEW_TABLES <= set(inspect(bind).get_table_names())
            assert previous_rows(bind) == before
            with bind.connect() as connection:
                assert connection.execute(text("SELECT count(*) FROM visitor_grants")).scalar_one() == 0
                assert connection.execute(text("SELECT count(*) FROM visitor_claims")).scalar_one() == 0
    finally:
        db.rollback()
        command.upgrade(config, "head")
    assert previous_rows(bind) == before


def test_partial_ddl_failure_restores_old_revision_and_does_not_leave_grants_or_modify_previous_rows(db, access_world):
    seed_prior_credentials(db, access_world)
    bind, config = db.get_bind(), Config("alembic.ini")
    before = previous_rows(bind)
    try:
        command.downgrade(config, "0080_visitor_auth_credentials")
        with bind.begin() as connection:
            connection.execute(text("CREATE TABLE visitor_claims (sentinel integer NOT NULL)"))
            connection.execute(text("INSERT INTO visitor_claims VALUES (42)"))
        with pytest.raises(ProgrammingError):
            command.upgrade(config, "0081_visitor_access")
        assert not inspect(bind).has_table("visitor_grants")
        with bind.connect() as connection:
            assert connection.execute(text("SELECT sentinel FROM visitor_claims")).scalars().all() == [42]
            assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == "0080_visitor_auth_credentials"
        assert previous_rows(bind) == before
    finally:
        db.rollback()
        with bind.begin() as connection:
            connection.execute(text("DROP TABLE IF EXISTS visitor_claims"))
        command.upgrade(config, "head")
    assert previous_rows(bind) == before
