"""Real monthly snapshots, immutable documents and independent durable retries."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timedelta
from decimal import Decimal
from threading import Barrier

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import event, inspect, text, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.api.core import finance_archive as archives
from app.db.models.audit_log import AuditLog
from app.db.models.business import Business
from app.db.models.client import Client
from app.db.models.expense import Expense
from app.db.models.expense_category import ExpenseCategory
from app.db.models.financial_archive import FinancialArchive, FinancialArchiveJob
from app.db.models.visit import Visit
from app.db.session import SessionLocal

NOW = datetime(2026, 11, 2, 12)
START = datetime(2026, 10, 1)


@pytest.fixture
def world(db, make_user, monkeypatch):
    monkeypatch.setattr(archives, 'utcnow', lambda: NOW)
    owner = make_user('business_owner')
    business = Business(name='Archive business', business_type='salon', currency='CZK')
    client = Client(full_name='Archive client')
    db.add_all([business, client]); db.flush(); owner.business_id = business.id
    category = ExpenseCategory(name='Archive costs', business_id=business.id)
    db.add(category); db.flush()
    visits = []
    for index, (unit, price, status, deleted) in enumerate([
        ('EUR', 100.10, 'completed', False), ('CZK', 2000.05, 'completed', False),
        (None, 42.25, 'completed', False), ('EUR', 9999, 'completed', True),
        ('EUR', 500, 'planned', False),
    ]):
        row = Visit(business_id=business.id, client_id=client.id, specialist_id=owner.id,
            date_start=START + timedelta(days=5, hours=index), status=status, price=price,
            currency_snapshot=unit, deleted_at=NOW if deleted else None)
        db.add(row); visits.append(row)
    expenses = []
    for unit, value, category_id in [('EUR', 7.25, category.id), ('CZK', 52.05, category.id), ('EUR', 5.25, None)]:
        row = Expense(business_id=business.id, amount=value, currency=unit,
                      expense_date=START + timedelta(days=5), category_id=category_id)
        db.add(row); expenses.append(row)
    db.commit()
    return {'business': business.id, 'owner': owner.id, 'client': client.id,
            'visits': [row.id for row in visits], 'expenses': [row.id for row in expenses]}


def summary_by_unit(payload):
    return {row['currency']: row for row in payload['summary']['by_currency']}


def test_generator_has_exact_currency_slices_and_all_counts_in_one_select(db, world):
    statements = []
    def capture(_connection, _cursor, statement, *_args):
        statements.append(statement)
    event.listen(db.get_bind(), 'before_cursor_execute', capture)
    try:
        payload = archives.archive_monthly_financial_data(db, 2026, 10, world['business'])
    finally:
        event.remove(db.get_bind(), 'before_cursor_execute', capture)
    assert len(statements) == 1 and statements[0].lstrip().upper().startswith('WITH')
    units = summary_by_unit(payload)
    assert units['EUR']['revenue'] == '100.10'
    assert units['EUR']['expenses'] == '12.50' and units['EUR']['profit'] == '87.60'
    assert units['CZK']['revenue'] == '2000.05' and units['CZK']['profit'] == '1948.00'
    assert units[None]['revenue'] is None and units[None]['requires_review']
    assert payload['summary']['total_visits'] == 4 and payload['summary']['completed_visits'] == 3
    assert payload['summary']['requires_review']
    assert 'revenue' not in payload['summary']
    assert payload['value_basis'] == 'quoted_service_price' and payload['schema_version'] == 2
    assert not db.new and not db.dirty


def test_projection_keeps_counts_and_money_before_a_concurrent_commit(db, world):
    engine = db.get_bind(); changed = []
    def change(_connection, _cursor, statement, *_args):
        if changed or not statement.lstrip().upper().startswith('WITH'):
            return
        changed.append(True)
        with Session(engine) as writer:
            writer.execute(update(Visit).where(Visit.id == world['visits'][0]).values(status='cancelled', price=999))
            writer.execute(update(Expense).where(Expense.id == world['expenses'][0]).values(amount=700))
            writer.commit()
    event.listen(engine, 'after_cursor_execute', change)
    try:
        payload = archives.archive_monthly_financial_data(db, 2026, 10, world['business'])
    finally:
        event.remove(engine, 'after_cursor_execute', change)
    assert changed
    assert payload['summary']['completed_visits'] == 3
    assert summary_by_unit(payload)['EUR']['revenue'] == '100.10'
    assert summary_by_unit(payload)['EUR']['expenses'] == '12.50'


def test_generator_never_flushes_pending_application_changes(db, world):
    pending = Client(full_name='Pending caller-owned client'); db.add(pending)
    archives.archive_monthly_financial_data(db, 2026, 10, world['business'])
    assert pending in db.new and pending.id is None
    with pytest.raises(ValueError, match='pending application changes'):
        archives.save_monthly_archive(db, 2026, 10, world['business'])
    assert pending in db.new and pending.id is None
    db.rollback()


def test_last_microsecond_of_december_is_included_and_soft_deletes_are_not(db, world):
    db.execute(update(Visit).where(Visit.id == world['visits'][0]).values(date_start=datetime(2025, 12, 31, 23, 59, 59, 999999)))
    db.commit()
    payload = archives.archive_monthly_financial_data(db, 2025, 12, world['business'])
    assert payload['period']['to'] == '2025-12-31T23:59:59.999999Z'
    assert payload['summary']['total_visits'] == payload['summary']['completed_visits'] == 1
    assert summary_by_unit(payload)['EUR']['revenue'] == '100.10'


def test_existing_document_is_not_recalculated_and_caller_owns_rollback(db, world):
    row, created = archives.save_monthly_archive(db, 2026, 10, world['business'])
    assert created
    with Session(db.get_bind()) as observer:
        assert observer.query(FinancialArchive).count() == 0
    db.rollback()
    row, created = archives.save_monthly_archive(db, 2026, 10, world['business']); db.commit()
    original = deepcopy(row.payload); identifier = row.id
    db.execute(update(Visit).where(Visit.id == world['visits'][0]).values(price=999)); db.commit()
    row, created = archives.save_monthly_archive(db, 2026, 10, world['business']); db.commit()
    assert not created and row.id == identifier and row.payload == original


@pytest.mark.parametrize('operation', ['payload', 'period', 'delete'])
def test_database_forbids_mutating_saved_documents(db, world, operation):
    row, _ = archives.save_monthly_archive(db, 2026, 10, world['business']); db.commit()
    before = deepcopy(row.payload)
    with pytest.raises(DBAPIError, match='append-only'):
        if operation == 'delete': db.delete(row)
        elif operation == 'period': row.month = 9
        else: row.payload = {**before, 'changed': True}
        db.commit()
    db.rollback(); db.refresh(row)
    assert row.payload == before and row.month == 10


def test_payload_must_match_its_business_and_period(db, world):
    payload = archives.archive_monthly_financial_data(db, 2026, 10, world['business'])
    payload['business_id'] = world['business'] + 100
    db.add(FinancialArchive(business_id=world['business'], year=2026, month=10,
                            schema_version=2, payload=payload))
    with pytest.raises(DBAPIError): db.commit()
    db.rollback(); assert db.query(FinancialArchive).count() == 0


def test_parallel_direct_saves_create_one_document(db, world):
    business_id = world['business']; db.rollback(); gate = Barrier(2)
    def save(_index):
        with SessionLocal() as session:
            gate.wait(timeout=10)
            row, created = archives.save_monthly_archive(session, 2026, 10, business_id)
            session.commit()
            return row.id, created
    with ThreadPoolExecutor(max_workers=2) as pool: results = list(pool.map(save, range(2)))
    assert results[0][0] == results[1][0]
    assert sum(created for _, created in results) == 1
    assert db.query(FinancialArchive).count() == 1


def test_partial_failure_retries_only_the_failed_business_on_the_second_day(db, world, monkeypatch):
    other = Business(name='Other archive', business_type='salon'); db.add(other); db.commit(); other_id = other.id
    original = archives.archive_monthly_financial_data
    def fail_first(session, year, month, business_id, **kwargs):
        if business_id == world['business']: raise RuntimeError('PRIVATE_TEST_DETAIL')
        return original(session, year, month, business_id, **kwargs)
    monkeypatch.setattr(archives, 'archive_monthly_financial_data', fail_first)
    with pytest.raises(archives.ArchiveBatchError): archives.archive_all_businesses_last_month(db)
    assert db.query(FinancialArchive).one().business_id == other_id
    pending = db.query(FinancialArchiveJob).filter_by(business_id=world['business']).one()
    assert pending.status == 'pending' and pending.attempts == 1
    assert pending.last_error == 'archive_generation_failed' and 'PRIVATE' not in pending.last_error
    assert pending.next_attempt_at == NOW + timedelta(hours=1)
    assert archives.archive_all_businesses_last_month(db) == []
    monkeypatch.setattr(archives, 'archive_monthly_financial_data', original)
    monkeypatch.setattr(archives, 'utcnow', lambda: NOW + timedelta(days=1))
    assert archives.should_run_monthly_archive(db)
    assert len(archives.archive_all_businesses_last_month(db)) == 1
    assert db.query(FinancialArchive).count() == 2
    assert not archives.should_run_monthly_archive(db)
    assert archives.archive_all_businesses_last_month(db) == []


def test_pending_period_survives_the_change_of_month(db, world, monkeypatch):
    original = archives.archive_monthly_financial_data
    def fail_projection(*_args, **_kwargs):
        raise RuntimeError('test')
    monkeypatch.setattr(archives, 'archive_monthly_financial_data', fail_projection)
    with pytest.raises(archives.ArchiveBatchError): archives.archive_all_businesses_last_month(db)
    monkeypatch.setattr(archives, 'archive_monthly_financial_data', original)
    monkeypatch.setattr(archives, 'utcnow', lambda: datetime(2026, 12, 2, 12))
    assert len(archives.archive_all_businesses_last_month(db)) == 2
    assert {(row.year, row.month) for row in db.query(FinancialArchive)} == {(2026, 10), (2026, 11)}


def test_deferred_commit_failure_keeps_the_document_absent_and_the_job_retryable(db, world):
    db.execute(text("CREATE FUNCTION reject_archive_test() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'deferred archive failure'; END $$"))
    db.execute(text('CREATE CONSTRAINT TRIGGER reject_archive_test AFTER INSERT ON financial_archives DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION reject_archive_test()')); db.commit()
    try:
        with pytest.raises(archives.ArchiveBatchError): archives.archive_all_businesses_last_month(db)
        assert db.query(FinancialArchive).count() == 0
        job = db.query(FinancialArchiveJob).one()
        assert job.status == 'pending' and job.attempts == 1
    finally:
        db.rollback()
        db.execute(text('DROP TRIGGER reject_archive_test ON financial_archives'))
        db.execute(text('DROP FUNCTION reject_archive_test()')); db.commit()


def test_two_queue_workers_do_not_duplicate_documents(db, world):
    other = Business(name='Second worker business', business_type='salon'); db.add(other); db.commit(); gate = Barrier(2)
    def run(_index):
        with SessionLocal() as session:
            gate.wait(timeout=10)
            return len(archives.archive_all_businesses_last_month(session))
    with ThreadPoolExecutor(max_workers=2) as pool: results = list(pool.map(run, range(2)))
    assert sum(results) == 2 and db.query(FinancialArchive).count() == 2
    assert db.query(FinancialArchiveJob).filter_by(status='done').count() == 2


def test_deleted_business_cancels_its_pending_work(db, world):
    archives.schedule_previous_month(db); db.commit()
    db.execute(update(Business).where(Business.id == world['business']).values(deleted_at=NOW)); db.commit()
    assert archives.archive_all_businesses_last_month(db) == []
    assert db.query(FinancialArchiveJob).one().status == 'cancelled'
    assert db.query(FinancialArchive).count() == 0
    assert not archives.should_run_monthly_archive(db)


def test_legacy_global_audit_marker_cannot_hide_missing_documents(db, world):
    db.add(AuditLog(entity_type='finance_archive_job', entity_id=0, action='monthly_finance_archive',
                    changes={'archived_businesses': 99}, created_at=NOW)); db.commit()
    assert archives.should_run_monthly_archive(db)
    assert len(archives.archive_all_businesses_last_month(db)) == 1
    assert db.query(AuditLog).count() == 1


def test_empty_migration_roundtrip_keeps_legacy_audit_without_inventing_archives(db):
    marker = AuditLog(entity_type='finance_archive_job', entity_id=0, action='monthly_finance_archive',
                      changes={'archived_businesses': 99}); db.add(marker); db.commit(); identifier = marker.id; db.close()
    config = Config('alembic.ini')
    try:
        command.downgrade(config, '0087_package_coverage')
        assert not inspect(db.get_bind()).has_table('financial_archives')
        command.upgrade(config, 'head')
        assert db.query(FinancialArchive).count() == db.query(FinancialArchiveJob).count() == 0
        assert db.get(AuditLog, identifier).changes == {'archived_businesses': 99}
    finally:
        db.rollback(); db.close(); command.upgrade(config, 'head')


@pytest.mark.parametrize('kind', ['document', 'pending_job'])
def test_downgrade_refuses_to_lose_documents_or_pending_work(db, world, kind):
    if kind == 'document': archives.save_monthly_archive(db, 2026, 10, world['business'])
    else: archives.schedule_previous_month(db)
    db.commit(); db.close()
    with pytest.raises(DBAPIError, match='Financial archive data exists'):
        command.downgrade(Config('alembic.ini'), '0087_package_coverage')
    assert db.execute(text('SELECT version_num FROM alembic_version')).scalar_one() == '0091_visitor_proof_mail_jobs'
    db.rollback()


@pytest.mark.parametrize('year,month', [(2026, 11), (2027, 1)])
def test_unfinished_month_cannot_be_saved_as_a_final_document(db, world, year, month):
    with pytest.raises(ValueError, match='complete months'):
        archives.save_monthly_archive(db, year, month, world['business'])
    assert db.query(FinancialArchive).count() == 0


def test_soft_deletion_does_not_hide_an_existing_document_from_idempotent_replay(db, world):
    original, _ = archives.save_monthly_archive(db, 2026, 10, world['business']); db.commit()
    identifier, payload = original.id, deepcopy(original.payload)
    db.execute(update(Business).where(Business.id == world['business']).values(deleted_at=NOW)); db.commit()
    replay, created = archives.save_monthly_archive(db, 2026, 10, world['business'])
    assert replay is not None and replay.id == identifier and replay.payload == payload
    assert not created


def test_restoring_a_business_requeues_its_cancelled_period(db, world):
    archives.schedule_previous_month(db); db.commit()
    db.execute(update(Business).where(Business.id == world['business']).values(deleted_at=NOW)); db.commit()
    assert archives.archive_all_businesses_last_month(db) == []
    db.execute(update(Business).where(Business.id == world['business']).values(deleted_at=None)); db.commit()
    assert archives.should_run_monthly_archive(db)
    assert len(archives.archive_all_businesses_last_month(db)) == 1
    assert db.query(FinancialArchiveJob).one().status == 'done'


def test_a_repeatedly_failing_job_cannot_occupy_a_small_batch_ahead_of_new_work(db, world, monkeypatch):
    archives.schedule_previous_month(db); db.commit()
    job = db.query(FinancialArchiveJob).one()
    job.attempts = 10; job.last_error = 'archive_generation_failed'
    job.next_attempt_at = NOW - timedelta(hours=1)
    other = Business(name='Unblocked business', business_type='salon'); db.add(other); db.commit(); other_id = other.id
    original = archives.archive_monthly_financial_data
    def fail_old(session, year, month, business_id, **kwargs):
        if business_id == world['business']: raise RuntimeError('old job still failing')
        return original(session, year, month, business_id, **kwargs)
    monkeypatch.setattr(archives, 'archive_monthly_financial_data', fail_old)
    monkeypatch.setattr(archives, 'BATCH_LIMIT', 1)
    documents = archives.archive_all_businesses_last_month(db)
    assert len(documents) == 1 and documents[0]['business_id'] == other_id
    assert db.query(FinancialArchiveJob).filter_by(business_id=world['business']).one().status == 'pending'


def test_database_uniqueness_is_per_business_and_month(db, world):
    original, _ = archives.save_monthly_archive(db, 2026, 10, world['business']); db.commit()
    duplicate = FinancialArchive(business_id=world['business'], year=2026, month=10,
                                 schema_version=2, payload=deepcopy(original.payload))
    db.add(duplicate)
    with pytest.raises(DBAPIError, match='uq_financial_archive_period'): db.commit()
    db.rollback(); assert db.query(FinancialArchive).count() == 1
    unique = inspect(db.get_bind()).get_unique_constraints('financial_archives')
    assert any(row['column_names'] == ['business_id', 'year', 'month'] for row in unique)
