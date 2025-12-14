from sqlalchemy import Column, Integer, ForeignKey, DateTime, String
from sqlalchemy.orm import relationship

from app.db.base import Base


class BlockSlot(Base):
    __tablename__ = "block_slots"

    id = Column(Integer, primary_key=True, index=True)
    specialist_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    start = Column(DateTime, nullable=False)
    end = Column(DateTime, nullable=False)
    reason = Column(String, nullable=True)

    specialist = relationship("User")











