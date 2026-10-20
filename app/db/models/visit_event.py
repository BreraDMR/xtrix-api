"""Lifecycle events for a visit, recorded with the state change in one transaction."""
from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, UniqueConstraint

from app.api.core.clock import utcnow
from app.db.base import Base


class VisitEvent(Base):
    __tablename__ = "visit_events"
    __table_args__ = (
        UniqueConstraint("event_key", name="uq_visit_event_key"),
    )

    id = Column(Integer, primary_key=True, index=True)
    business_id = Column(Integer, ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False, index=True)
    visit_id = Column(Integer, ForeignKey("visits.id", ondelete="CASCADE"), nullable=False, index=True)
    event_type = Column(String(50), nullable=False, index=True)
    actor = Column(String(20), nullable=False, default="client", server_default="client")
    event_key = Column(String(120), nullable=False)
    created_at = Column(DateTime, nullable=False, default=utcnow, index=True)
