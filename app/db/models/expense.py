from datetime import datetime
from sqlalchemy import Column, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import relationship
from app.db.base import Base
class Expense(Base):
    __tablename__ = "expenses"
    id = Column(Integer, primary_key=True, index=True)
    business_id = Column(Integer, ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False, index=True)
    category_id = Column(Integer, ForeignKey("expense_categories.id", ondelete="SET NULL"), nullable=True, index=True)
    amount = Column(Float, nullable=False, index=True)
    currency = Column(String, default="EUR", nullable=False, index=True)
    description = Column(Text, nullable=True)
    expense_date = Column(DateTime, nullable=False, index=True)  
    payment_method = Column(String, nullable=False, default="other", index=True)  
    created_by_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, index=True)
    business = relationship("Business", lazy="select")
    category = relationship("ExpenseCategory", back_populates="expenses", lazy="select")
    created_by = relationship("User", foreign_keys=[created_by_id], lazy="select")
