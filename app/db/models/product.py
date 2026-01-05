from datetime import datetime
from sqlalchemy import Column, DateTime, Float, ForeignKey, Integer, String, Text, Boolean
from sqlalchemy.orm import relationship

from app.db.base import Base


class Product(Base):
    """
    Модель для товаров на складе.
    Каждый товар привязан к бизнесу (business_id).
    """
    __tablename__ = "products"

    id = Column(Integer, primary_key=True, index=True)
    business_id = Column(Integer, ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String, nullable=False, index=True)
    description = Column(Text, nullable=True)
    unit = Column(String, nullable=False, default="шт")  # единица измерения (шт, мл, гр и т.д.)
    quantity = Column(Float, default=0.0, nullable=False, index=True)  # текущее количество
    min_quantity = Column(Float, default=0.0, nullable=False, index=True)  # минимальный остаток для уведомления
    price_per_unit = Column(Float, nullable=True)  # цена за единицу (опционально)
    category = Column(String, nullable=True, index=True)  # категория товара
    barcode = Column(String, nullable=True, unique=True, index=True)  # штрихкод
    supplier = Column(String, nullable=True)  # поставщик
    location = Column(String, nullable=True)  # место хранения на складе
    is_active = Column(Boolean, default=True, nullable=False, index=True)  # активен ли товар
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, index=True)

    # Relationships
    business = relationship("Business", lazy="select")
    usages = relationship("ProductUsage", back_populates="product", lazy="dynamic", cascade="all,delete")


