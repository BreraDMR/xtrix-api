"""A recorded manual payment survives status edits and direct SQL writes."""
from datetime import datetime, timedelta

import pytest
from sqlalchemy import text

from app.db.models.visit import Visit
from tests import test_visitor_confirm_core as core

world=core.world


def marker(db,visit_id):
    return db.execute(text('SELECT manual_payment_recorded_at FROM visits WHERE id=:id'),{'id':visit_id}).scalar_one()


@pytest.mark.parametrize('method',['cash','card','other'])
def test_completed_manual_payment_gets_a_database_marker_that_cannot_be_cleared(db,world,method):
    visit_id=world['visit'];assert marker(db,visit_id) is None
    db.execute(text("UPDATE visits SET status='completed',payment_method=:method WHERE id=:id"),{'method':method,'id':visit_id});db.commit()
    first=marker(db,visit_id);assert isinstance(first,datetime)
    db.execute(text("UPDATE visits SET status='planned',payment_method=NULL,manual_payment_recorded_at=NULL WHERE id=:id"),{'id':visit_id});db.commit()
    assert marker(db,visit_id)==first
    db.execute(text('UPDATE visits SET manual_payment_recorded_at=:other WHERE id=:id'),{'other':first+timedelta(days=10),'id':visit_id});db.commit()
    assert marker(db,visit_id)==first


@pytest.mark.parametrize('status,price,method',[
    ('planned',999,'cash'),('completed',0,'cash'),('completed',None,'cash'),
    ('completed',999,None),('completed',999,'unknown'),('completed',float('nan'),'cash'),
    ('completed',float('inf'),'cash'),
])
def test_incomplete_or_nonfinite_evidence_does_not_invent_payment(db,world,status,price,method):
    db.execute(text('UPDATE visits SET status=:status,price=:price,payment_method=:method,manual_payment_recorded_at=:fake WHERE id=:id'),
        dict(status=status,price=price,method=method,fake=datetime(2000,1,1),id=world['visit']));db.commit()
    assert marker(db,world['visit']) is None


def test_rollback_removes_uncommitted_manual_payment_evidence(db,world):
    db.execute(text("UPDATE visits SET status='completed',payment_method='cash' WHERE id=:id"),{'id':world['visit']})
    assert marker(db,world['visit']) is not None
    db.rollback();assert marker(db,world['visit']) is None


def test_marker_is_not_part_of_public_visit_schemas():
    from app.schemas.visit import VisitOut
    from app.schemas.visitor_history import VisitorHistoryVisit
    assert 'manual_payment_recorded_at' not in VisitOut.model_fields
    assert 'manual_payment_recorded_at' not in VisitorHistoryVisit.model_fields


def test_0082_0083_roundtrip_preserves_old_rows_and_backfills_only_proven_current_manual_payment(db,world):
    import json
    from alembic import command
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    from sqlalchemy import MetaData,inspect,select
    bind=db.get_bind();config=Config('alembic.ini');db.rollback()
    def old_rows():
        metadata=MetaData();metadata.reflect(bind=bind)
        with bind.connect() as conn:
            return {t.name:sorted(json.dumps(dict(r),default=str,sort_keys=True) for r in conn.execute(select(*[
                c for c in t.c if not((t.name=='visits' and c.name=='manual_payment_recorded_at') or (t.name=='loyalty_transactions' and c.name in {'cashback_currency','cashback_amount_exact','cashback_balance_after_exact','discount_currency','discount_amount_exact'}) or (t.name=='employees' and c.name in {'base_salary_currency','base_salary_exact'}) or (t.name=='salary_calculations' and c.name in {'currency','salary_period_id','business_id_snapshot','total_revenue_exact','commission_amount_exact','base_salary_exact','total_salary_exact','adjustments_total_exact','total_with_adjustments_exact','paid_amount_exact'}) or (t.name in {'salary_adjustments','salary_payments'} and c.name in {'currency','amount_exact'}) or (t.name=='expenses' and c.name=='salary_payment_id'))])).mappings())
                for t in metadata.sorted_tables if t.name not in {'alembic_version','client_cashback_balances','salary_periods'}}
    assert ScriptDirectory.from_config(config).get_heads()==['0086_payroll_currency_snapshot']
    try:
        command.downgrade(config,'0082_visit_currency_snapshot')
        with bind.begin() as conn:
            conn.execute(text("UPDATE visits SET status='completed',payment_method='card' WHERE id=:id"),{'id':world['visit']})
        with bind.begin() as conn:
            for days,status,price,method in [(10,'planned',999,'cash'),(11,'completed',0,'card'),(12,'completed',999,None)]:
                conn.execute(text('''INSERT INTO visits (business_id,client_id,specialist_id,date_start,date_end,status,price,payment_method)
                    SELECT business_id,client_id,specialist_id,date_start+(:days*interval '1 day'),
                    date_end+(:days*interval '1 day'),:status,:price,:method FROM visits WHERE id=:id'''),
                    dict(days=days,status=status,price=price,method=method,id=world['visit']))
        before=old_rows()
        for _ in range(2):
            command.upgrade(config,'0083_manual_payment_marker')
            assert old_rows()==before
            with bind.connect() as conn:
                assert conn.execute(text('SELECT count(*) FROM visits WHERE manual_payment_recorded_at IS NOT NULL')).scalar_one()==1
                assert conn.execute(text('SELECT manual_payment_recorded_at FROM visits WHERE id=:id'),{'id':world['visit']}).scalar_one() is not None
            command.downgrade(config,'0082_visit_currency_snapshot')
            assert 'manual_payment_recorded_at' not in {c['name'] for c in inspect(bind).get_columns('visits')}
            assert old_rows()==before
    finally:
        db.rollback();command.upgrade(config,'head')


def test_partial_marker_ddl_failure_rolls_back_column_and_backfill(db,world):
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import inspect
    from sqlalchemy.exc import ProgrammingError
    bind=db.get_bind();config=Config('alembic.ini');db.rollback()
    try:
        command.downgrade(config,'0082_visit_currency_snapshot')
        with bind.begin() as conn:
            conn.execute(text("CREATE FUNCTION visits_record_manual_payment() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RETURN NEW; END $$"))
        with pytest.raises(ProgrammingError):command.upgrade(config,'0083_manual_payment_marker')
        assert 'manual_payment_recorded_at' not in {c['name'] for c in inspect(bind).get_columns('visits')}
        with bind.connect() as conn:
            assert conn.execute(text('SELECT version_num FROM alembic_version')).scalar_one()=='0082_visit_currency_snapshot'
            assert conn.execute(text('SELECT count(*) FROM visits WHERE id=:id'),{'id':world['visit']}).scalar_one()==1
    finally:
        db.rollback()
        with bind.begin() as conn:conn.execute(text('DROP FUNCTION IF EXISTS visits_record_manual_payment()'))
        command.upgrade(config,'head')
