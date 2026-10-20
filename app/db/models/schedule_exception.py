from sqlalchemy import Boolean, CheckConstraint, Column, Date, ForeignKey, Integer, String, Time, UniqueConstraint
from sqlalchemy.orm import relationship
from app.db.base import Base


class ScheduleException(Base):
    __tablename__ = "schedule_exceptions"
    __table_args__ = (
        UniqueConstraint("specialist_id", "exception_date", name="uq_schedule_exception_specialist_date"),
        CheckConstraint(
            "(is_closed = true AND start_time IS NULL AND end_time IS NULL)"
            " OR (is_closed = false AND start_time IS NOT NULL AND end_time IS NOT NULL"
            " AND start_time < end_time)",
            name="ck_schedule_exception_window",
        ),
    )
    id = Column(Integer, primary_key=True, index=True)
    specialist_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    exception_date = Column(Date, nullable=False, index=True)
    is_closed = Column(Boolean, nullable=False, default=True, server_default="true")
    # null on a closed day, custom working hours otherwise
    start_time = Column(Time, nullable=True)
    end_time = Column(Time, nullable=True)
    reason = Column(String(255), nullable=True)
    specialist = relationship("User")
