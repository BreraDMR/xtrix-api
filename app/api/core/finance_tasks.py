"""The scheduled entrypoint only runs the persistent archive queue."""
from app.db.session import SessionLocal
from app.api.core.finance_archive import archive_all_businesses_last_month, should_run_monthly_archive
from app.api.core.logger import logger


def run_monthly_finance_archive():
    db = SessionLocal()
    try:
        if not should_run_monthly_archive(db):
            return 0
        archives = archive_all_businesses_last_month(db)
        logger.info('Monthly finance archive: saved %s documents', len(archives))
        return len(archives)
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def check_and_run_monthly_archive():
    return run_monthly_finance_archive()
