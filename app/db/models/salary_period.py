"""One immutable salary policy snapshot shared by all currency calculations."""
from sqlalchemy import CheckConstraint, Column, DateTime, ForeignKey, Integer, Numeric, String, UniqueConstraint
from app.api.core.clock import utcnow
from app.db.base import Base


class SalaryPeriod(Base):
    __tablename__ = 'salary_periods'
    __table_args__ = (
        UniqueConstraint('business_id', 'employee_id', 'period_start', 'period_end', name='uq_salary_period_scope'),
        CheckConstraint('period_end > period_start', name='ck_salary_period_dates'),
        CheckConstraint('base_salary_exact BETWEEN 0 AND 9999999999999999.99 AND ((base_salary_currency IS NULL AND base_salary_exact = 0) OR (base_salary_currency IS NOT NULL AND base_salary_currency COLLATE "C" ~ \'^[A-Z]{3}$\'))', name='ck_salary_period_base'),
        CheckConstraint('commission_percentage BETWEEN 0 AND 100', name='ck_salary_period_commission'),
    )
    id = Column(Integer, primary_key=True)
    business_id = Column(Integer, ForeignKey('businesses.id', ondelete='RESTRICT'), nullable=False)
    employee_id = Column(Integer, ForeignKey('employees.id', ondelete='RESTRICT'), nullable=False)
    period_start = Column(DateTime, nullable=False)
    period_end = Column(DateTime, nullable=False)
    base_salary_currency = Column(String(3), nullable=True)
    base_salary_exact = Column(Numeric(18, 2), nullable=False)
    commission_percentage = Column(Numeric(), nullable=False)
    created_at = Column(DateTime, default=utcnow, nullable=False)
    created_by_id = Column(Integer, ForeignKey('users.id', ondelete='SET NULL'), nullable=True)
