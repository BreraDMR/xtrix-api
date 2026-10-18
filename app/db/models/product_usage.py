from sqlalchemy import Column, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import relationship
from app.db.base import Base
from app.api.core.clock import utcnow
class ProductUsage(Base):
    __tablename__ = "product_usages"
    id = Column(Integer, primary_key=True, index=True)
    product_id = Column(Integer, ForeignKey("products.id", ondelete="CASCADE"), nullable=False, index=True)
    visit_id = Column(Integer, ForeignKey("visits.id", ondelete="SET NULL"), nullable=True, index=True)  
    business_id = Column(Integer, ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False, index=True)
    quantity = Column(Float, nullable=False, index=True)  
    used_by_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)  
    client_id = Column(Integer, ForeignKey("clients.id", ondelete="SET NULL"), nullable=True, index=True)  
    notes = Column(Text, nullable=True)  
    # "visit" when a visit wrote the product off by itself
    source = Column(String, nullable=True)
    usage_date = Column(DateTime, default=utcnow, nullable=False, index=True)  
    created_at = Column(DateTime, default=utcnow, index=True)
    product = relationship("Product", back_populates="usages", lazy="select")
    visit = relationship("Visit", lazy="select")
    business = relationship("Business", lazy="select")
    used_by = relationship("User", foreign_keys=[used_by_id], lazy="select")
    client = relationship("Client", lazy="select")
