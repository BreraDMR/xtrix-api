from datetime import datetime
from sqlalchemy import Column, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import relationship

from app.db.base import Base


class ProductUsage(Base):
    """
    Модель для использования товаров.
    Записывает каждое списание товара (например, при визите).
    """
    __tablename__ = "product_usages"

    id = Column(Integer, primary_key=True, index=True)
    product_id = Column(Integer, ForeignKey("products.id", ondelete="CASCADE"), nullable=False, index=True)
    visit_id = Column(Integer, ForeignKey("visits.id", ondelete="SET NULL"), nullable=True, index=True)  # может быть без визита
    business_id = Column(Integer, ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False, index=True)
    quantity = Column(Float, nullable=False, index=True)  # количество использованных единиц
    used_by_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)  # кто использовал
    client_id = Column(Integer, ForeignKey("clients.id", ondelete="SET NULL"), nullable=True, index=True)  # для какого клиента
    notes = Column(Text, nullable=True)  # примечания
    usage_date = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)  # когда использовано
    created_at = Column(DateTime, default=datetime.utcnow, index=True)

    # Relationships
    product = relationship("Product", back_populates="usages", lazy="select")
    visit = relationship("Visit", lazy="select")
    business = relationship("Business", lazy="select")
    used_by = relationship("User", foreign_keys=[used_by_id], lazy="select")
    client = relationship("Client", lazy="select")

