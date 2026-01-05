from datetime import datetime
from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Boolean
from sqlalchemy.orm import relationship

from app.db.base import Base


class ExpenseCategory(Base):
    """
    Модель для категорий расходов.
    Поддерживает как системные, так и пользовательские категории.
    """
    __tablename__ = "expense_categories"

    id = Column(Integer, primary_key=True, index=True)
    business_id = Column(Integer, ForeignKey("businesses.id", ondelete="CASCADE"), nullable=True, index=True)  # null = системная категория
    name = Column(String, nullable=False, index=True)
    type = Column(String, nullable=False, default="variable", index=True)  # fixed или variable
    color = Column(String, nullable=True)  # hex цвет для графиков (например, "#3B82F6")
    is_system = Column(Boolean, default=False, nullable=False, index=True)  # системная категория
    is_active = Column(Boolean, default=True, nullable=False, index=True)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)

    # Relationships
    business = relationship("Business", lazy="select")
    expenses = relationship("Expense", back_populates="category", lazy="dynamic", cascade="all,delete")

