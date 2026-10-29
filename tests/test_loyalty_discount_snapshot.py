"""A bonus receipt keeps its monetary meaning after the conversion rate changes."""
from decimal import Decimal

import pytest
from sqlalchemy import inspect

from app.api.core import loyalty_logic
from app.db.models.loyalty_program import LoyaltyProgram, LoyaltyTransaction
from tests import test_loyalty_visit_quote_lock as quote


@pytest.fixture
def world(db,monkeypatch):
    return quote.world.__wrapped__(db,monkeypatch)


def test_bonus_receipt_stores_the_exact_applied_discount_and_visit_unit(db,world):
    program=db.get(LoyaltyProgram,world['program']);program.currency_per_point=0.4;db.commit()
    success,discount,error=loyalty_logic.use_points_for_visit(db,world['client'],world['business'],5,3,visit_id=world['visit'])
    assert success and discount==5,error
    receipt=db.query(LoyaltyTransaction).one()
    assert receipt.discount_currency=='EUR' and receipt.discount_amount_exact==Decimal('5.00')
    program=db.get(LoyaltyProgram,world['program']);program.currency_per_point=100;db.commit();db.expire_all()
    receipt=db.query(LoyaltyTransaction).one()
    assert receipt.discount_amount_exact==Decimal('5.00') and receipt.points_amount==-5


def test_cashback_receipt_has_a_saved_monetary_allocation(db,world):
    assert loyalty_logic.use_points_for_visit(db,world['client'],world['business'],0,Decimal('10.01'),visit_id=world['visit'])[0]
    receipt=db.query(LoyaltyTransaction).one()
    assert receipt.discount_currency=='EUR' and receipt.discount_amount_exact==Decimal('10.01')


def test_discount_schema_has_nullable_legacy_fields(db):
    columns={c['name']:c for c in inspect(db.get_bind()).get_columns('loyalty_transactions')}
    assert columns['discount_currency']['nullable'] and columns['discount_amount_exact']['nullable']


def test_new_point_spend_requires_an_explicit_monetary_receipt(db,world):
    from app.api.crud import crud_loyalty
    from app.api.core.cashback_wallets import CashbackError
    from app.db.models.client_business_relation import ClientBusinessRelation
    with pytest.raises(CashbackError,match='loyalty_payment_quote_required'):
        crud_loyalty.create_loyalty_transaction(db,world['client'],world['business'],'spent',-5,visit_id=world['visit'])
    assert db.query(LoyaltyTransaction).count()==0
    assert db.query(ClientBusinessRelation).one().loyalty_points==100


def test_0084_0085_roundtrip_preserves_legacy_amounts_without_guessing_a_conversion(db,world):
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import text
    db.add(LoyaltyTransaction(client_id=world['client'],business_id=world['business'],visit_id=world['visit'],
        transaction_type='spent',points_amount=-5,points_balance_after=95,cashback_amount=0,cashback_balance_after=0))
    db.commit();config=Config('alembic.ini');bind=db.get_bind();db.rollback()
    def snapshot():
        with bind.connect() as conn:
            return [dict(row) for row in conn.execute(text('SELECT id,client_id,business_id,visit_id,transaction_type,points_amount,points_balance_after,cashback_amount,cashback_balance_after FROM loyalty_transactions')).mappings()]
    before=snapshot()
    try:
        for _round in range(2):
            command.downgrade(config,'0084_cashback_currency_wallets');assert snapshot()==before
            command.upgrade(config,'head');assert snapshot()==before
            with bind.connect() as conn:
                assert conn.execute(text('SELECT discount_currency,discount_amount_exact FROM loyalty_transactions')).one()==(None,None)
    finally:command.upgrade(config,'head')


def test_downgrade_cannot_discard_a_recorded_bonus_payment_amount(db,world):
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import text
    from sqlalchemy.exc import DBAPIError
    assert loyalty_logic.use_points_for_visit(db,world['client'],world['business'],5,3,visit_id=world['visit'])[0]
    db.rollback()
    with pytest.raises(DBAPIError,match='Bonus payment amounts exist'):
        command.downgrade(Config('alembic.ini'),'0084_cashback_currency_wallets')
    with db.get_bind().connect() as conn:
        assert conn.execute(text('SELECT version_num FROM alembic_version')).scalar_one()=='0086_payroll_currency_snapshot'
        assert conn.execute(text('SELECT discount_currency,discount_amount_exact FROM loyalty_transactions')).one()==('EUR',Decimal('8.00'))


@pytest.mark.parametrize('unit,amount',[(None,5),('EUR',None),('eur',5),('EUR',-1),('EUR','NaN'),('EUR',0)])
def test_database_rejects_incomplete_or_invalid_monetary_bonus_snapshots(db,world,unit,amount):
    from sqlalchemy import text
    from sqlalchemy.exc import IntegrityError
    row=LoyaltyTransaction(client_id=world['client'],business_id=world['business'],visit_id=world['visit'],
        transaction_type='spent',points_amount=-5,points_balance_after=95,cashback_amount=0,cashback_balance_after=0)
    db.add(row);db.commit();row_id=row.id
    with pytest.raises(IntegrityError):
        db.execute(text('UPDATE loyalty_transactions SET discount_currency=:unit,discount_amount_exact=:amount WHERE id=:id'),
            {'unit':unit,'amount':amount,'id':row_id});db.commit()
    db.rollback();db.expire_all()
    assert db.get(LoyaltyTransaction,row_id).discount_amount_exact is None
