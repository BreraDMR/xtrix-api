"""Currency wallets add explicit units without relabelling legacy money."""
from sqlalchemy import inspect,text


def test_known_cashback_wallet_table_starts_without_guessed_legacy_units(db):
    assert db.execute(text('SELECT count(*) FROM client_cashback_balances')).scalar_one()==0


def test_ledger_currency_is_nullable_without_a_default(db):
    columns={c['name']:c for c in inspect(db.get_bind()).get_columns('loyalty_transactions')}
    assert 'cashback_currency' in columns
    assert columns['cashback_currency']['nullable'] and columns['cashback_currency']['default'] is None


from datetime import datetime
from decimal import Decimal
import json

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
import pytest
from sqlalchemy import MetaData,select
from sqlalchemy.exc import DBAPIError,IntegrityError

from app.db.models.client_cashback_balance import ClientCashbackBalance
from app.db.models.client_business_relation import ClientBusinessRelation
from app.db.models.loyalty_program import LoyaltyTransaction
from tests import test_visitor_confirm_core as core

world=core.world
NEW_COLUMNS={'cashback_currency','cashback_amount_exact','cashback_balance_after_exact'}


def old_rows(bind):
    metadata=MetaData();metadata.reflect(bind=bind)
    with bind.connect() as conn:
        return {table.name:sorted(json.dumps(dict(row),sort_keys=True,default=str) for row in conn.execute(
            select(*[c for c in table.c if not(table.name=='loyalty_transactions' and c.name in NEW_COLUMNS)])).mappings())
            for table in metadata.sorted_tables if table.name not in {'alembic_version','client_cashback_balances'}}


def test_0083_0084_roundtrip_keeps_legacy_amounts_and_does_not_guess_units(db,world):
    old_amount=123.456789012345
    db.add(ClientBusinessRelation(client_id=world['client'],business_id=world['business'],loyalty_points=7,
        loyalty_cashback=old_amount))
    db.add(LoyaltyTransaction(client_id=world['client'],business_id=world['business'],visit_id=world['visit'],
        transaction_type='earned',points_amount=7,cashback_amount=old_amount,points_balance_after=7,cashback_balance_after=old_amount))
    db.commit();bind=db.get_bind();config=Config('alembic.ini');db.rollback()
    assert ScriptDirectory.from_config(config).get_heads()==['0084_cashback_currency_wallets']
    before=old_rows(bind)
    try:
        for _ in range(2):
            command.downgrade(config,'0083_manual_payment_marker');assert old_rows(bind)==before
            command.upgrade(config,'0084_cashback_currency_wallets');assert old_rows(bind)==before
            with bind.connect() as conn:
                assert conn.execute(text('SELECT count(*) FROM client_cashback_balances')).scalar_one()==0
                assert conn.execute(text('SELECT cashback_currency,cashback_amount_exact,cashback_balance_after_exact FROM loyalty_transactions')).one()==(None,None,None)
                assert conn.execute(text('SELECT loyalty_cashback FROM client_business_relations')).scalar_one()==old_amount
    finally:db.rollback();command.upgrade(config,'head')


def test_two_known_currencies_remain_separate_with_exact_cents(db,world):
    for currency,amount in [('CZK','100.01'),('EUR','2.99')]:
        db.add(ClientCashbackBalance(business_id=world['business'],client_id=world['client'],currency=currency,balance=Decimal(amount)))
    db.commit()
    assert {r.currency:r.balance for r in db.query(ClientCashbackBalance)}=={'CZK':Decimal('100.01'),'EUR':Decimal('2.99')}
    with pytest.raises(IntegrityError):
        with db.begin_nested():
            db.add(ClientCashbackBalance(business_id=world['business'],client_id=world['client'],currency='EUR',balance=1));db.flush()
    assert db.query(ClientCashbackBalance).count()==2


@pytest.mark.parametrize('currency,amount',[(None,'1.00'),('eur','1.00'),('EUR','-1.00'),('EUR','NaN')])
def test_unknown_or_invalid_known_wallet_values_are_rejected(db,world,currency,amount):
    with pytest.raises(DBAPIError):
        with db.begin_nested():
            db.execute(text('''INSERT INTO client_cashback_balances (business_id,client_id,currency,balance,updated_at)
                VALUES (:business,:client,:currency,:balance,:now)'''),
                {**world,'currency':currency,'balance':amount,'now':datetime.utcnow()})
    assert db.query(ClientCashbackBalance).count()==0


def test_known_wallet_data_blocks_destructive_schema_downgrade(db,world):
    db.add(ClientCashbackBalance(business_id=world['business'],client_id=world['client'],currency='CZK',balance=Decimal('12.34')));db.commit();db.rollback()
    config=Config('alembic.ini')
    with pytest.raises(DBAPIError):command.downgrade(config,'0083_manual_payment_marker')
    with db.get_bind().connect() as conn:
        assert conn.execute(text('SELECT version_num FROM alembic_version')).scalar_one()=='0084_cashback_currency_wallets'
        assert conn.execute(text('SELECT currency,balance FROM client_cashback_balances')).one()==('CZK',Decimal('12.34'))


@pytest.mark.parametrize('kind',['missing_exact','unknown_with_exact','negative_balance'])
def test_ledger_cannot_relabel_money_without_complete_known_snapshot(db,world,kind):
    values={'currency':'EUR','amount':Decimal('1.23'),'balance':Decimal('1.23')}
    if kind=='missing_exact':values['amount']=None
    elif kind=='unknown_with_exact':values['currency']=None
    else:values['balance']=Decimal('-1.23')
    with pytest.raises(DBAPIError):
        with db.begin_nested():
            db.execute(text('''INSERT INTO loyalty_transactions (client_id,business_id,visit_id,transaction_type,
                points_amount,cashback_amount,points_balance_after,cashback_balance_after,
                cashback_currency,cashback_amount_exact,cashback_balance_after_exact)
                VALUES (:client,:business,:visit,'earned',0,1.23,0,1.23,:currency,:amount,:balance)'''),{**world,**values})
