from sqlalchemy import Column, DateTime, ForeignKey, Integer, Numeric, String, UniqueConstraint
from sqlalchemy.orm import relationship
from app.db.base import Base


class VisitItem(Base):
    """One service inside a visit chain.

    Price, duration and buffers are snapshots: editing a service later must not move or
    re-price a visit that was already sold. Visit.price stays the single total that
    analytics, loyalty and payroll read, so item rows never double-count revenue.
    """

    __tablename__ = "visit_items"
    __table_args__ = (
        UniqueConstraint("visit_id", "position", name="uq_visit_item_position"),
    )

    id = Column(Integer, primary_key=True, index=True)
    visit_id = Column(
        Integer,
        ForeignKey("visits.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    position = Column(Integer, nullable=False)
    service_id = Column(Integer, ForeignKey("services.id"), nullable=True, index=True)
    specialist_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    service_name = Column(String, nullable=True)
    price = Column(Numeric(10, 2), nullable=True)
    duration_minutes = Column(Integer, nullable=False)
    buffer_before_minutes = Column(Integer, nullable=False, default=0, server_default="0")
    buffer_after_minutes = Column(Integer, nullable=False, default=0, server_default="0")
    date_start = Column(DateTime, nullable=False, index=True)
    date_end = Column(DateTime, nullable=False, index=True)

    visit = relationship("Visit", back_populates="items")
