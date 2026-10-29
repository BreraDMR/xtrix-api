"""A currency column adds evidence storage without inventing evidence for old money."""
from datetime import datetime, timedelta
from decimal import Decimal
import json

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
import pytest
from sqlalchemy import MetaData, inspect, select, text, update
from sqlalchemy.exc import DBAPIError

from app.db.models.business import Business
from app.db.models.client import Client
from app.db.models.gift import GiftCertificate, GiftLedgerEntry, GiftOffer, GiftOrder
from app.db.models.service_payment import ServicePaymentAttempt, ServicePaymentRefund
from app.db.models.user import User
from app.db.models.visit import Visit

NOW = datetime(2026, 10, 26, 12)
REVISION = "0082_visit_currency_snapshot"
PREVIOUS = "0081_visitor_access"


def test_visit_model_declares_nullable_currency_without_python_or_server_default():
    assert "currency_snapshot" in Visit.__table__.c
    column = Visit.__table__.c.currency_snapshot
    assert column.nullable and column.type.length == 3
    assert column.default is None and column.server_default is None
    assert "ck_visits_currency_snapshot" in {constraint.name for constraint in Visit.__table__.constraints}


def test_real_schema_has_nullable_currency_and_ascii_check_without_default(db):
    columns = {column["name"]: column for column in inspect(db.get_bind()).get_columns("visits")}
    assert "currency_snapshot" in columns
    column = columns["currency_snapshot"]
    assert column["nullable"] and column["type"].length == 3 and column["default"] is None
    checks = {constraint["name"]: constraint["sqltext"] for constraint in inspect(db.get_bind()).get_check_constraints("visits")}
    assert "ck_visits_currency_snapshot" in checks


@pytest.fixture
def financial_world(db):
    business = Business(name="Currency snapshot salon", business_type="salon", currency="CZK")
    db.add(business)
    db.flush()
    staff = User(full_name="Snapshot owner", email="snapshot-owner@example.com", hashed_password="synthetic",
                 role="business_owner", business_id=business.id)
    client = Client(full_name="Snapshot client", contact_email="snapshot-client@example.com")
    db.add_all([staff, client])
    db.flush()
    visit = Visit(business_id=business.id, client_id=client.id, specialist_id=staff.id,
                  date_start=NOW, date_end=NOW + timedelta(minutes=30), price=1996.50,
                  status="cancelled", payment_method="card", note="unchanged-money-evidence")
    db.add(visit)
    db.flush()
    attempt = ServicePaymentAttempt(business_id=business.id, visit_id=visit.id, provider="sandbox", merchant="test-salon",
        request_key="snapshot-deposit", public_ref="snapshot-deposit", public_token_hash="a" * 64,
        status="paid", currency="CZK", amount=Decimal("496.50"), remaining_amount=Decimal("1500.00"),
        refunded_amount=Decimal("100.00"), paid_at=NOW, expires_at=NOW + timedelta(minutes=30))
    db.add(attempt)
    db.flush()
    db.add(ServicePaymentRefund(business_id=business.id, attempt_id=attempt.id, provider="sandbox",
        refund_key="snapshot-refund", amount=Decimal("100.00"), currency="CZK"))
    offer = GiftOffer(business_id=business.id, name="Snapshot gift", kind="money", price=Decimal("100.00"),
                      currency="CZK", face_value=Decimal("100.00"), validity_days=30)
    db.add(offer)
    db.flush()
    order = GiftOrder(business_id=business.id, offer_id=offer.id, provider="sandbox", merchant="test-salon",
        request_key="snapshot-gift", request_hash="b" * 64, public_ref="snapshot-gift", public_token_hash="c" * 64,
        status="paid", offer_snapshot={"name": "Snapshot gift", "kind": "money", "currency": "CZK", "face_value": "100.00", "validity_days": 30},
        amount=Decimal("100.00"), currency="CZK", buyer_name="Snapshot buyer", buyer_email="buyer@example.com",
        expires_at=NOW + timedelta(minutes=30), paid_at=NOW)
    db.add(order)
    db.flush()
    certificate = GiftCertificate(business_id=business.id, offer_id=offer.id, order_id=order.id,
        code="SNAPSHOT-CODE", code_hash="d" * 64, code_last4="CODE", kind="money", status="active",
        issuer_name=business.name, offer_name=offer.name, currency="CZK", face_value=Decimal("100.00"),
        visits_total=0, recipient_name="Snapshot client", buyer_name="Snapshot buyer", buyer_email="buyer@example.com",
        issued_at=NOW, expires_at=NOW + timedelta(days=30))
    db.add(certificate)
    db.flush()
    db.add(GiftLedgerEntry(certificate_id=certificate.id, business_id=business.id, entry_type="redeemed",
        amount_delta=Decimal("-50.00"), units_delta=0, request_key="snapshot-redeem", visit_id=visit.id))
    result = {"business": business.id, "client": client.id, "staff": staff.id, "visit": visit.id}
    db.commit()
    db.rollback()
    return result


@pytest.mark.parametrize("currency", [None, "CZK", "EUR", "USD", "ZZZ"])
def test_database_accepts_nullable_or_three_uppercase_ascii_letters_without_normalizing(db, financial_world, currency):
    visit_id = financial_world["visit"]
    db.execute(text("UPDATE visits SET currency_snapshot=:currency WHERE id=:id"), {"currency": currency, "id": visit_id})
    db.commit()
    assert db.execute(text("SELECT currency_snapshot FROM visits WHERE id=:id"), {"id": visit_id}).scalar_one() == currency


@pytest.mark.parametrize("currency", ["", "czk", "Czk", " CZ", "CZ ", "EU", "EURO", "123", "ČZK", "ＣＺＫ", "A\nB", "USD\n", "ßAA", "US💵"])
def test_database_rejects_invalid_currency_code_without_app_validator(db, financial_world, currency):
    visit_id = financial_world["visit"]
    with pytest.raises(DBAPIError) as error:
        with db.begin_nested():
            db.execute(text("UPDATE visits SET currency_snapshot=:currency WHERE id=:id"), {"currency": currency, "id": visit_id})
    assert error.value.orig.pgcode in {"23514", "22001"}
    if error.value.orig.pgcode == "23514":
        assert error.value.orig.diag.constraint_name == "ck_visits_currency_snapshot"
    assert db.execute(text("SELECT currency_snapshot FROM visits WHERE id=:id"), {"id": visit_id}).scalar_one() is None


def test_orm_and_sql_new_visits_without_explicit_snapshot_remain_unknown(db, financial_world):
    world = financial_world
    visit = Visit(business_id=world["business"], client_id=world["client"], specialist_id=world["staff"],
                  date_start=NOW, date_end=NOW + timedelta(minutes=30), price=42, status="cancelled")
    db.add(visit)
    db.commit()
    assert visit.currency_snapshot is None
    db.execute(text("""INSERT INTO visits (business_id,client_id,specialist_id,date_start,date_end,price,status)
        VALUES (:business,:client,:staff,:start,:finish,43,'cancelled')"""),
        {**world, "start": NOW, "finish": NOW + timedelta(minutes=30)})
    db.commit()
    assert db.execute(text("SELECT currency_snapshot FROM visits")).scalars().all() == [None, None, None]


def test_business_currency_changes_do_not_rewrite_explicit_or_unknown_snapshot(db, financial_world):
    world = financial_world
    db.execute(update(Visit).where(Visit.id == world["visit"]).values(currency_snapshot="CZK"))
    db.commit()
    db.execute(update(Business).where(Business.id == world["business"]).values(currency="EUR"))
    db.commit()
    assert db.get(Visit, world["visit"]).currency_snapshot == "CZK"
    assert db.get(Visit, world["visit"]).price == 1996.50
    assert db.query(ServicePaymentAttempt).one().currency == db.query(GiftCertificate).one().currency == "CZK"


def previous_columns_snapshot(bind):
    metadata = MetaData()
    metadata.reflect(bind=bind)
    with bind.connect() as connection:
        result = {}
        for table in metadata.sorted_tables:
            if table.name in {"alembic_version", "client_cashback_balances", "salary_periods"}:
                continue
            columns = [column for column in table.c if not ((table.name == "visits" and column.name in {"currency_snapshot", "manual_payment_recorded_at"}) or (table.name == "loyalty_transactions" and column.name in {"cashback_currency", "cashback_amount_exact", "cashback_balance_after_exact", "discount_currency", "discount_amount_exact"}) or (table.name == "employees" and column.name in {"base_salary_currency", "base_salary_exact"}) or (table.name == "salary_calculations" and column.name in {"currency", "salary_period_id", "business_id_snapshot", "total_revenue_exact", "commission_amount_exact", "base_salary_exact", "total_salary_exact", "adjustments_total_exact", "total_with_adjustments_exact", "paid_amount_exact"}) or (table.name in {"salary_adjustments", "salary_payments"} and column.name in {"currency", "amount_exact"}) or (table.name == "expenses" and column.name == "salary_payment_id"))]
            result[table.name] = sorted(json.dumps(dict(row), sort_keys=True, default=str)
                for row in connection.execute(select(*columns)).mappings())
        return result


def test_0081_0082_roundtrip_preserves_every_old_column_payment_and_gift_and_backfills_nothing(db, financial_world):
    world = financial_world
    bind, config = db.get_bind(), Config("alembic.ini")
    assert ScriptDirectory.from_config(config).get_heads() == ["0086_payroll_currency_snapshot"]
    db.rollback()
    before = previous_columns_snapshot(bind)
    try:
        command.downgrade(config, PREVIOUS)
        assert "currency_snapshot" not in {column["name"] for column in inspect(bind).get_columns("visits")}
        assert previous_columns_snapshot(bind) == before
        # This is the ordinary 0081 schema with real visits/items/payments/gift rows already present.
        for _ in range(2):
            command.upgrade(config, REVISION)
            assert previous_columns_snapshot(bind) == before
            with bind.connect() as connection:
                assert connection.execute(text("SELECT currency_snapshot FROM visits")).scalars().all() == [None]
                assert connection.execute(text("SELECT price FROM visits WHERE id=:id"), {"id": world["visit"]}).scalar_one() == 1996.50
                assert connection.execute(text("SELECT currency FROM service_payment_attempts")).scalar_one() == "CZK"
                assert connection.execute(text("SELECT currency FROM gift_certificates")).scalar_one() == "CZK"
            command.downgrade(config, PREVIOUS)
            assert previous_columns_snapshot(bind) == before
        command.upgrade(config, REVISION)
    finally:
        db.rollback()
        command.upgrade(config, "head")
    assert previous_columns_snapshot(bind) == before


def test_partial_check_creation_failure_rolls_back_added_column_and_keeps_previous_rows(db, financial_world):
    bind, config = db.get_bind(), Config("alembic.ini")
    db.rollback()
    before = previous_columns_snapshot(bind)
    try:
        command.downgrade(config, PREVIOUS)
        with bind.begin() as connection:
            connection.execute(text("ALTER TABLE visits ADD CONSTRAINT ck_visits_currency_snapshot CHECK (TRUE)"))
        with pytest.raises(DBAPIError):
            command.upgrade(config, REVISION)
        assert "currency_snapshot" not in {column["name"] for column in inspect(bind).get_columns("visits")}
        with bind.connect() as connection:
            assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == PREVIOUS
        assert previous_columns_snapshot(bind) == before
    finally:
        db.rollback()
        with bind.begin() as connection:
            connection.execute(text("ALTER TABLE visits DROP CONSTRAINT IF EXISTS ck_visits_currency_snapshot"))
        command.upgrade(config, "head")
    assert previous_columns_snapshot(bind) == before
