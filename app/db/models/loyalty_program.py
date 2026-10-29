from sqlalchemy import CheckConstraint, Numeric, Column, DateTime, Float, ForeignKey, Integer, String, Boolean, Text, Date
from sqlalchemy.orm import relationship
from app.db.base import Base
from app.api.core.clock import utcnow
class LoyaltyProgram(Base):
    __tablename__ = "loyalty_programs"
    id = Column(Integer, primary_key=True, index=True)
    business_id = Column(Integer, ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False, unique=True, index=True)
    program_type = Column(String, nullable=False, default='points', index=True)  
    points_per_currency = Column(Float, nullable=False, default=1.0)  
    points_name = Column(String, nullable=True, default='points')  
    currency_per_point = Column(Float, nullable=True)  
    cashback_percent = Column(Float, nullable=True, default=0.0)  
    use_levels = Column(Boolean, default=False, nullable=False)
    points_expiry_days = Column(Integer, nullable=True)
    birthday_bonus_enabled = Column(Boolean, default=False, nullable=False)
    birthday_bonus_points = Column(Float, nullable=True)
    birthday_bonus_percent = Column(Float, nullable=True)  
    is_active = Column(Boolean, default=True, nullable=False, index=True)
    max_bonus_payment_percent = Column(Float, nullable=True, default=100.0)  
    created_at = Column(DateTime, default=utcnow, index=True)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow, index=True)
    business = relationship("Business", lazy="select")
    levels = relationship("LoyaltyLevel", back_populates="program", lazy="dynamic", cascade="all,delete", order_by="LoyaltyLevel.min_spending")
    promotions = relationship("LoyaltyPromotion", back_populates="program", lazy="dynamic", cascade="all,delete")
class LoyaltyLevel(Base):
    __tablename__ = "loyalty_levels"
    id = Column(Integer, primary_key=True, index=True)
    program_id = Column(Integer, ForeignKey("loyalty_programs.id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String, nullable=False, index=True)  
    level_number = Column(Integer, nullable=False, index=True)  
    min_spending = Column(Float, nullable=False, index=True)  
    points_multiplier = Column(Float, nullable=True, default=1.0)  
    cashback_multiplier = Column(Float, nullable=True, default=1.0)  
    benefits = Column(Text, nullable=True)  
    color = Column(String, nullable=True)  
    created_at = Column(DateTime, default=utcnow, index=True)
    program = relationship("LoyaltyProgram", back_populates="levels", lazy="select")
class LoyaltyPromotion(Base):
    __tablename__ = "loyalty_promotions"
    id = Column(Integer, primary_key=True, index=True)
    program_id = Column(Integer, ForeignKey("loyalty_programs.id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String, nullable=False, index=True)
    description = Column(Text, nullable=True)
    promotion_type = Column(String, nullable=False, index=True)
    points_multiplier = Column(Float, nullable=True)  
    cashback_percent = Column(Float, nullable=True)  
    bonus_points = Column(Float, nullable=True)  
    discount_percent = Column(Float, nullable=True)  
    service_ids = Column(Text, nullable=True)  
    min_amount = Column(Float, nullable=True)  
    start_date = Column(Date, nullable=False, index=True)
    end_date = Column(Date, nullable=False, index=True)
    client_id = Column(Integer, ForeignKey("clients.id", ondelete="CASCADE"), nullable=True, index=True)
    is_active = Column(Boolean, default=True, nullable=False, index=True)
    created_at = Column(DateTime, default=utcnow, index=True)
    program = relationship("LoyaltyProgram", back_populates="promotions", lazy="select")
    client = relationship("Client", lazy="select")
class LoyaltyTransaction(Base):
    __tablename__ = "loyalty_transactions"
    __table_args__ = (CheckConstraint(
        """(cashback_currency IS NULL AND cashback_amount_exact IS NULL AND cashback_balance_after_exact IS NULL)
        OR (cashback_currency IS NOT NULL AND cashback_currency COLLATE "C" ~ '^[A-Z]{3}$'
            AND cashback_amount_exact IS NOT NULL AND cashback_balance_after_exact IS NOT NULL
            AND cashback_amount_exact BETWEEN -9999999999999999.99 AND 9999999999999999.99
            AND cashback_balance_after_exact BETWEEN 0 AND 9999999999999999.99)""",
        name='ck_loyalty_cashback_snapshot'), CheckConstraint(
        """(discount_currency IS NULL AND discount_amount_exact IS NULL)
        OR (discount_currency IS NOT NULL AND discount_currency COLLATE "C" ~ '^[A-Z]{3}$'
            AND discount_amount_exact IS NOT NULL AND discount_amount_exact > 0
            AND discount_amount_exact <= 9999999999999999.99 AND transaction_type = 'spent'
            AND points_amount <= 0 AND coalesce(cashback_amount_exact, 0) <= 0
        AND (points_amount < 0 OR coalesce(cashback_amount_exact, 0) < 0)
            AND discount_amount_exact >= -coalesce(cashback_amount_exact, 0)
            AND (cashback_currency IS NULL OR cashback_currency = discount_currency))""",
        name='ck_loyalty_discount_snapshot'),)
    discount_currency = Column(String(3), nullable=True)
    discount_amount_exact = Column(Numeric(18, 2), nullable=True)
    # Legacy float amounts remain untouched and have no inferred unit.
    cashback_currency = Column(String(3), nullable=True)
    cashback_amount_exact = Column(Numeric(18, 2), nullable=True)
    cashback_balance_after_exact = Column(Numeric(18, 2), nullable=True)
    id = Column(Integer, primary_key=True, index=True)
    client_id = Column(Integer, ForeignKey("clients.id", ondelete="CASCADE"), nullable=False, index=True)
    business_id = Column(Integer, ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False, index=True)
    program_id = Column(Integer, ForeignKey("loyalty_programs.id", ondelete="SET NULL"), nullable=True, index=True)
    transaction_type = Column(String, nullable=False, index=True)
    points_amount = Column(Float, nullable=False)
    cashback_amount = Column(Float, nullable=True, default=0.0)
    points_balance_after = Column(Float, nullable=False)
    cashback_balance_after = Column(Float, nullable=True, default=0.0)
    visit_id = Column(Integer, ForeignKey("visits.id", ondelete="SET NULL"), nullable=True, index=True)
    promotion_id = Column(Integer, ForeignKey("loyalty_promotions.id", ondelete="SET NULL"), nullable=True, index=True)
    level_id = Column(Integer, ForeignKey("loyalty_levels.id", ondelete="SET NULL"), nullable=True, index=True)
    description = Column(Text, nullable=True)
    expiry_date = Column(Date, nullable=True, index=True)
    created_at = Column(DateTime, default=utcnow, index=True)
    client = relationship("Client", lazy="select")
    business = relationship("Business", lazy="select")
    program = relationship("LoyaltyProgram", lazy="select")
    visit = relationship("Visit", lazy="select")
    promotion = relationship("LoyaltyPromotion", lazy="select")
    level = relationship("LoyaltyLevel", lazy="select")
