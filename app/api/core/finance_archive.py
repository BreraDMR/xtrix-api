"""Store monthly currency snapshots once; retry unfinished businesses independently."""
from copy import deepcopy
from datetime import date, datetime, timedelta
from decimal import Decimal
import zlib

from sqlalchemy import and_, exists, or_, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.api.core.clock import utcnow
from app.api.core.expense_reports import get_profit_projection
from app.api.core.logger import logger
from app.db.models.business import Business
from app.db.models.financial_archive import FinancialArchive, FinancialArchiveJob
from app.schemas.datetimes import utc_iso

SCHEMA_VERSION = 2
LOCK_NAMESPACE = 1178686019  # FARC, separate from the other application locks.
RETRY_DELAY = timedelta(hours=1)
BATCH_LIMIT = 500


class ArchiveBatchError(RuntimeError):
    def __init__(self, failed_job_ids):
        self.failed_job_ids = tuple(failed_job_ids)
        super().__init__(f'Financial archive generation failed for {len(failed_job_ids)} jobs')


def _period(year, month):
    if type(year) is not int or type(month) is not int or not 1 <= year <= 9998 or not 1 <= month <= 12:
        raise ValueError('A valid archive year and month are required')
    start = datetime(year, month, 1)
    following = datetime(year + 1, 1, 1) if month == 12 else datetime(year, month + 1, 1)
    return start, following - timedelta(microseconds=1)


def _business_id(value):
    if type(value) is not int or not 1 <= value <= 2_147_483_647:
        raise ValueError('An explicit positive business_id is required')
    return value


def _clean_session(db):
    if db.new or db.dirty or db.deleted:
        raise ValueError('Archive jobs require a session without pending application changes')


def _json_value(value):
    if isinstance(value, Decimal):
        return format(value, 'f')
    if isinstance(value, datetime):
        return utc_iso(value)
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    return value


def archive_monthly_financial_data(db: Session, year: int, month: int, business_id=None,
                                   *, archived_at=None) -> dict:
    """Pure projection; monetary dimensions and visit counts share one SQL snapshot."""
    business_id = _business_id(business_id)
    start, end = _period(year, month)
    report = get_profit_projection(db, business_id, start, end, include_visit_counts=True)
    # The archive stores the exact slices, not the report's legacy float compatibility totals.
    dimensions = ('currency', 'requires_review', 'by_currency')
    summary = {key: report[key] for key in dimensions}
    summary.update(total_visits=report['total_visits'], completed_visits=report['completed_visits'])
    payload = {'schema_version': SCHEMA_VERSION, 'business_id': business_id,
        'period': {'year': year, 'month': month, 'from': utc_iso(start), 'to': utc_iso(end)},
        'value_basis': report['value_basis'], 'summary': summary,
        'daily': [{key: row[key] for key in ('date', *dimensions)} for row in report['daily']],
        'expenses_by_category': [{key: row[key] for key in
            ('category_id', 'category_name', 'category_code', 'color', *dimensions)}
            for row in report['expenses_by_category']],
        'archived_at': utc_iso(archived_at or utcnow())}
    return _json_value(payload)


def save_monthly_archive(db: Session, year: int, month: int, business_id: int):
    """Caller owns commit/rollback. No existing document is ever regenerated."""
    _clean_session(db)
    business_id = _business_id(business_id)
    _, end = _period(year, month)
    now = utcnow()
    if end >= now:
        raise ValueError('Only complete months can be archived')
    key = zlib.crc32(f'{business_id}:{year}:{month}'.encode())
    if key >= 2**31:
        key -= 2**32
    db.execute(text('SELECT pg_advisory_xact_lock(:namespace, :key)'),
               {'namespace': LOCK_NAMESPACE, 'key': key})
    existing = db.query(FinancialArchive).filter_by(business_id=business_id, year=year,
                                                   month=month).populate_existing().one_or_none()
    if existing is not None:
        return existing, False
    # Current business state cannot hide an already saved historical document.
    business = db.execute(select(Business.id).where(Business.id == business_id,
        Business.deleted_at.is_(None)).with_for_update(read=True)).scalar_one_or_none()
    if business is None:
        return None, False
    payload = archive_monthly_financial_data(db, year, month, business_id, archived_at=now)
    row = FinancialArchive(business_id=business_id, year=year, month=month,
                           schema_version=SCHEMA_VERSION, payload=payload, created_at=now)
    db.add(row)
    db.flush()
    return row, True


def _previous_month(now):
    return (now.year - 1, 12) if now.month == 1 else (now.year, now.month - 1)


def _finished_period(year, month):
    return or_(FinancialArchiveJob.year < year,
               and_(FinancialArchiveJob.year == year, FinancialArchiveJob.month <= month))


def schedule_previous_month(db: Session, now=None):
    """Persist the pending work first, so failures survive even a month rollover."""
    _clean_session(db)
    now = now or utcnow()
    year, month = _previous_month(now)
    archived = exists(select(FinancialArchive.id).where(FinancialArchive.business_id == Business.id,
        FinancialArchive.year == year, FinancialArchive.month == month))
    business_ids = db.execute(select(Business.id).where(Business.deleted_at.is_(None), ~archived)).scalars().all()
    for business_id in business_ids:
        statement = insert(FinancialArchiveJob).values(business_id=business_id, year=year, month=month,
            status='pending', attempts=0, next_attempt_at=now, last_error=None, created_at=now, updated_at=now)
        db.execute(statement.on_conflict_do_update(constraint='uq_financial_archive_job_period',
            set_={'status': 'pending', 'next_attempt_at': now, 'last_error': None, 'updated_at': now},
            where=FinancialArchiveJob.status == 'cancelled'))


def _record_failure(db, job_id, now):
    job = db.query(FinancialArchiveJob).filter_by(id=job_id, status='pending').populate_existing().with_for_update(
        skip_locked=True).one_or_none()
    if job is not None:
        job.attempts += 1
        job.last_error = 'archive_generation_failed'
        job.next_attempt_at = now + RETRY_DELAY
        job.updated_at = now
    db.commit()


def archive_all_businesses_last_month(db: Session) -> list[dict]:
    """Commit each business separately and report partial failures after trying the rest."""
    now = utcnow()
    schedule_previous_month(db, now)
    db.commit()
    year, month = _previous_month(now)
    job_ids = db.execute(select(FinancialArchiveJob.id).where(
        FinancialArchiveJob.status == 'pending', FinancialArchiveJob.next_attempt_at <= now,
        _finished_period(year, month)).order_by(FinancialArchiveJob.attempts,
            FinancialArchiveJob.next_attempt_at, FinancialArchiveJob.year,
            FinancialArchiveJob.month, FinancialArchiveJob.id).limit(BATCH_LIMIT)).scalars().all()
    documents, failed = [], []
    for job_id in job_ids:
        business_id = None
        try:
            job = db.query(FinancialArchiveJob).filter(FinancialArchiveJob.id == job_id,
                FinancialArchiveJob.status == 'pending', FinancialArchiveJob.next_attempt_at <= now,
                _finished_period(year, month)).populate_existing().with_for_update(skip_locked=True).one_or_none()
            if job is None:
                db.rollback()
                continue
            business_id = job.business_id
            archive, created = save_monthly_archive(db, job.year, job.month, business_id)
            job.attempts += 1
            job.updated_at = now
            job.next_attempt_at = None
            job.last_error = None
            job.status = 'done' if archive is not None else 'cancelled'
            payload = deepcopy(archive.payload) if created else None
            db.commit()
            if payload is not None:
                documents.append(payload)
        except Exception as error:
            db.rollback()
            failed.append(job_id)
            logger.error('Monthly archive failed: business=%s job=%s error_type=%s',
                         business_id, job_id, type(error).__name__)
            try:
                _record_failure(db, job_id, now)
            except Exception:
                db.rollback()
                logger.error('Monthly archive retry metadata could not be saved: job=%s', job_id)
    db.rollback()  # Release the read-only transaction when there was no eligible work.
    if failed:
        raise ArchiveBatchError(failed)
    return documents


def should_run_monthly_archive(db: Session) -> bool:
    _clean_session(db)
    now = utcnow()
    year, month = _previous_month(now)
    due = db.execute(select(FinancialArchiveJob.id).where(FinancialArchiveJob.status == 'pending',
        FinancialArchiveJob.next_attempt_at <= now, _finished_period(year, month)).limit(1)).first()
    if due is not None:
        return True
    archived = exists(select(FinancialArchive.id).where(FinancialArchive.business_id == Business.id,
        FinancialArchive.year == year, FinancialArchive.month == month))
    outstanding = exists(select(FinancialArchiveJob.id).where(FinancialArchiveJob.business_id == Business.id,
        FinancialArchiveJob.year == year, FinancialArchiveJob.month == month,
        FinancialArchiveJob.status != 'cancelled'))
    return db.execute(select(Business.id).where(Business.deleted_at.is_(None), ~archived,
                                               ~outstanding).limit(1)).first() is not None
