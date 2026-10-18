from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Boolean
from sqlalchemy.orm import relationship
from app.db.base import Base
from app.api.core.clock import utcnow
class ExpenseCategory(Base):
    __tablename__ = "expense_categories"
    id = Column(Integer, primary_key=True, index=True)
    business_id = Column(Integer, ForeignKey("businesses.id", ondelete="CASCADE"), nullable=True, index=True)  
    name = Column(String, nullable=False, index=True)
    # system categories have a code the frontend translates (rent, salaries...), own ones don't
    code = Column(String, nullable=True)
    type = Column(String, nullable=False, default="variable", index=True)  
    color = Column(String, nullable=True)  
    is_system = Column(Boolean, default=False, nullable=False, index=True)  
    is_active = Column(Boolean, default=True, nullable=False, index=True)
    created_at = Column(DateTime, default=utcnow, index=True)
    business = relationship("Business", lazy="select")
    expenses = relationship("Expense", back_populates="category", lazy="dynamic", cascade="all,delete")
