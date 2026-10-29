from sqlalchemy import CheckConstraint, Column, DateTime, Float, ForeignKey, Integer, String
from sqlalchemy.orm import relationship
from app.db.base import Base
from app.api.core.clock import utcnow
class Visit(Base):
    __tablename__ = "visits"
    __table_args__ = (
        CheckConstraint(
            """currency_snapshot IS NULL OR currency_snapshot COLLATE "C" ~ '^[A-Z]{3}$'""",
            name="ck_visits_currency_snapshot",
        ),
    )
    id = Column(Integer, primary_key=True, index=True)
    client_id = Column(Integer, ForeignKey("clients.id"), nullable=False, index=True)
    specialist_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    service_id = Column(Integer, ForeignKey("services.id"), nullable=True, index=True)
    business_id = Column(Integer, ForeignKey("businesses.id"), nullable=True, index=True)  
    date_start = Column(DateTime, nullable=False, index=True)
    date_end = Column(DateTime, nullable=True, index=True)
    service_name = Column(String, nullable=True)  
    note = Column(String, nullable=True)
    photo_url = Column(String, nullable=True)
    price = Column(Float, nullable=True, index=True)
    # NULL means the currency of these stored prices is not known.
    currency_snapshot = Column(String(3), nullable=True)
    # Database-owned evidence survives later status or payment-method edits.
    manual_payment_recorded_at = Column(DateTime, nullable=True)
    status = Column(String, default="planned", index=True)
    client_confirmed_at = Column(DateTime, nullable=True)
    payment_method = Column(String, nullable=True, index=True)  
    created_at = Column(DateTime, default=utcnow, index=True)
    deleted_at = Column(DateTime, nullable=True, index=True)  
    archived_at = Column(DateTime, nullable=True, index=True)  
    client = relationship("Client", back_populates="visits", lazy="select")
    specialist = relationship("User", back_populates="visits", lazy="select")
    service = relationship("Service", back_populates="visits", lazy="select")
    business = relationship("Business", lazy="select")
    items = relationship(
        "VisitItem",
        back_populates="visit",
        lazy="select",
        order_by="VisitItem.position",
        cascade="all, delete-orphan",
    )
    payment_attempts = relationship(
        "ServicePaymentAttempt",
        back_populates="visit",
        lazy="select",
        order_by="ServicePaymentAttempt.id",
    )
