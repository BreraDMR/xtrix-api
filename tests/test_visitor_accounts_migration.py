"""A migration roundtrip must leave every preexisting CRM/legacy-portal row unchanged."""
import json

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import MetaData, inspect, select, text

from app.api.core.clock import utcnow
from app.db.models.google_identity import GoogleIdentity
from app.db.models.visitor_account import VisitorAccount, VisitorGoogleIdentity, GOOGLE_ISSUER

NEW_TABLES = {"visitor_accounts", "visitor_google_identities"}


def legacy_rows(bind):
    metadata = MetaData()
    metadata.reflect(bind=bind)
    with bind.connect() as connection:
        return {table.name: sorted(json.dumps(dict(row), sort_keys=True, default=str)
                for row in connection.execute(select(*[column for column in table.c
                    if not ((table.name == "visits" and column.name in {"currency_snapshot", "manual_payment_recorded_at"}) or (table.name == "loyalty_transactions" and column.name in {"cashback_currency", "cashback_amount_exact", "cashback_balance_after_exact"}))])).mappings())
                for table in metadata.sorted_tables if table.name not in NEW_TABLES | {"visitor_challenges", "visitor_sessions", "visitor_grants", "visitor_claims", "alembic_version", "client_cashback_balances"}}


def test_visitor_creation_does_not_create_or_modify_a_crm_identity(db, make_user):
    user = make_user(email="same-address@example.com")
    user_id = user.id
    db.add(GoogleIdentity(user_id=user_id, subject="same-provider-subject"))
    db.commit()
    db.rollback()
    before = legacy_rows(db.get_bind())
    visitor = VisitorAccount(email_canonical=" SAME-ADDRESS@example.com ", email_verified_at=utcnow())
    db.add(visitor)
    db.flush()
    db.add(VisitorGoogleIdentity(account_id=visitor.id, issuer=GOOGLE_ISSUER, subject="same-provider-subject"))
    db.commit()
    db.rollback()
    assert legacy_rows(db.get_bind()) == before
    # Matching CRM email/Google subject still grants no CRM, business or client link.
    assert {fk.column.table.name for table in (VisitorAccount.__table__, VisitorGoogleIdentity.__table__)
            for fk in table.foreign_keys} == {"visitor_accounts"}


def test_0078_0079_roundtrip_has_one_head_and_preserves_all_legacy_rows(db, make_user):
    make_user()
    db.rollback()
    bind = db.get_bind()
    before = legacy_rows(bind)
    config = Config("alembic.ini")
    assert ScriptDirectory.from_config(config).get_heads() == ["0084_cashback_currency_wallets"]
    try:
        command.downgrade(config, "0078_waitlist_open_range")
        assert not (NEW_TABLES & set(inspect(bind).get_table_names()))
        assert legacy_rows(bind) == before
        command.upgrade(config, "0079_visitor_accounts")
        assert NEW_TABLES <= set(inspect(bind).get_table_names())
        assert legacy_rows(bind) == before
        with bind.connect() as connection:
            assert connection.execute(text("SELECT count(*) FROM visitor_accounts")).scalar() == 0
        command.downgrade(config, "0078_waitlist_open_range")
        assert legacy_rows(bind) == before
    finally:
        db.rollback()
        command.upgrade(config, "head")
    assert legacy_rows(bind) == before
