from sqlalchemy.orm import Session
from app.db.session import SessionLocal
from app.api.core.logger import logger
from app.api.core.finance_archive import archive_all_businesses_last_month, should_run_monthly_archive
from app.api.core.clock import utcnow
def run_monthly_finance_archive():
    db: Session = SessionLocal()
    try:
        if not should_run_monthly_archive(db):
            logger.info("Monthly archive not needed (not first day of month or already archived)")
            return 0
        logger.info("Starting monthly finance archive...")
        archives = archive_all_businesses_last_month(db)
        from app.db.models.audit_log import AuditLog
        audit_log = AuditLog(
            action='monthly_finance_archive',
            details={'archived_businesses': len(archives)},
            created_at=utcnow(),
        )
        db.add(audit_log)
        db.commit()
        logger.info(f"Monthly finance archive completed: {len(archives)} businesses archived")
        return len(archives)
    except Exception as e:
        logger.error(f"Error running monthly finance archive: {e}", exc_info=True)
        raise
    finally:
        db.close()
def check_and_run_monthly_archive():
    db: Session = SessionLocal()
    try:
        if should_run_monthly_archive(db):
            return run_monthly_finance_archive()
        return 0
    except Exception as e:
        logger.error(f"Error checking monthly archive: {e}", exc_info=True)
        raise
    finally:
        db.close()
