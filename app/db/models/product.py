from sqlalchemy import Column, DateTime, Float, ForeignKey, Integer, String, Text, Boolean, UniqueConstraint
from sqlalchemy.orm import relationship
from app.db.base import Base
from app.api.core.clock import utcnow
class Product(Base):
    __tablename__ = "products"
    # the same barcode can live in many salons, just once per salon
    __table_args__ = (UniqueConstraint("business_id", "barcode", name="uq_products_business_barcode"),)
    id = Column(Integer, primary_key=True, index=True)
    business_id = Column(Integer, ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String, nullable=False, index=True)
    description = Column(Text, nullable=True)
    unit = Column(String, nullable=False, default="pcs")  
    quantity = Column(Float, default=0.0, nullable=False, index=True)  
    min_quantity = Column(Float, default=0.0, nullable=False, index=True)  
    price_per_unit = Column(Float, nullable=True)  
    category = Column(String, nullable=True, index=True)  
    barcode = Column(String, nullable=True, index=True)  
    supplier = Column(String, nullable=True)  
    location = Column(String, nullable=True)  
    is_active = Column(Boolean, default=True, nullable=False, index=True)  
    created_at = Column(DateTime, default=utcnow, index=True)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow, index=True)
    business = relationship("Business", lazy="select")
    usages = relationship("ProductUsage", back_populates="product", lazy="dynamic", cascade="all,delete")
