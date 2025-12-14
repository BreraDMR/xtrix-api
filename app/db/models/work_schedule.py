from sqlalchemy import Column, Integer, ForeignKey, Time
from sqlalchemy.orm import relationship

from app.db.base import Base


class WorkSchedule(Base):
    __tablename__ = "work_schedules"

    id = Column(Integer, primary_key=True, index=True)
    specialist_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    weekday = Column(Integer, nullable=False)  # 0=Monday ... 6=Sunday
    start_time = Column(Time, nullable=False)
    end_time = Column(Time, nullable=False)

    specialist = relationship("User")











