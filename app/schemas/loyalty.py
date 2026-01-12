"""Schemas for loyalty program."""
from datetime import date, datetime
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field


# ============ LoyaltyProgram ============

class LoyaltyProgramBase(BaseModel):
    program_type: str = Field(..., description="Type: 'points', 'cashback', 'hybrid'")
    points_per_currency: float = Field(1.0, ge=0, description="Points per currency unit")
    points_name: Optional[str] = Field("points", description="Name of points (e.g., 'баллы', 'stars')")
    currency_per_point: Optional[float] = Field(None, ge=0, description="Currency value per point")
    cashback_percent: Optional[float] = Field(0.0, ge=0, le=100, description="Cashback percentage")
    use_levels: bool = Field(False, description="Enable loyalty levels")
    points_expiry_days: Optional[int] = Field(None, ge=1, description="Points expiry in days (null = no expiry)")
    birthday_bonus_enabled: bool = Field(False, description="Enable birthday bonuses")
    birthday_bonus_points: Optional[float] = Field(None, ge=0, description="Birthday bonus points")
    birthday_bonus_percent: Optional[float] = Field(None, ge=0, description="Birthday bonus cashback percent")
    is_active: bool = Field(True, description="Is program active")
    max_bonus_payment_percent: Optional[float] = Field(100.0, ge=0, le=100, description="Max % of check that can be paid with bonuses")


class LoyaltyProgramCreate(LoyaltyProgramBase):
    business_id: int


class LoyaltyProgramUpdate(BaseModel):
    program_type: Optional[str] = None
    points_per_currency: Optional[float] = Field(None, ge=0)
    points_name: Optional[str] = None
    currency_per_point: Optional[float] = Field(None, ge=0)
    cashback_percent: Optional[float] = Field(None, ge=0, le=100)
    use_levels: Optional[bool] = None
    points_expiry_days: Optional[int] = Field(None, ge=1)
    birthday_bonus_enabled: Optional[bool] = None
    birthday_bonus_points: Optional[float] = Field(None, ge=0)
    birthday_bonus_percent: Optional[float] = Field(None, ge=0)
    is_active: Optional[bool] = None
    max_bonus_payment_percent: Optional[float] = Field(None, ge=0, le=100)


class LoyaltyProgramOut(LoyaltyProgramBase):
    id: int
    business_id: int
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


# ============ LoyaltyLevel ============

class LoyaltyLevelBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    level_number: int = Field(..., ge=1, description="Level number (1, 2, 3...)")
    min_spending: float = Field(..., ge=0, description="Minimum spending for this level")
    points_multiplier: Optional[float] = Field(1.0, ge=0, description="Points multiplier")
    cashback_multiplier: Optional[float] = Field(1.0, ge=0, description="Cashback multiplier")
    benefits: Optional[str] = Field(None, description="Benefits description (JSON)")
    color: Optional[str] = Field(None, description="Color for UI")


class LoyaltyLevelCreate(LoyaltyLevelBase):
    program_id: int


class LoyaltyLevelUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=100)
    level_number: Optional[int] = Field(None, ge=1)
    min_spending: Optional[float] = Field(None, ge=0)
    points_multiplier: Optional[float] = Field(None, ge=0)
    cashback_multiplier: Optional[float] = Field(None, ge=0)
    benefits: Optional[str] = None
    color: Optional[str] = None


class LoyaltyLevelOut(LoyaltyLevelBase):
    id: int
    program_id: int
    created_at: datetime

    class Config:
        from_attributes = True


# ============ LoyaltyPromotion ============

class LoyaltyPromotionBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    description: Optional[str] = None
    promotion_type: str = Field(..., description="Type: 'double_points', 'extra_cashback', 'bonus_points', 'discount'")
    points_multiplier: Optional[float] = Field(None, ge=0)
    cashback_percent: Optional[float] = Field(None, ge=0)
    bonus_points: Optional[float] = Field(None, ge=0)
    discount_percent: Optional[float] = Field(None, ge=0, le=100)
    service_ids: Optional[str] = Field(None, description="JSON array of service IDs (null = all services)")
    min_amount: Optional[float] = Field(None, ge=0)
    start_date: date = Field(..., description="Promotion start date")
    end_date: date = Field(..., description="Promotion end date")
    client_id: Optional[int] = Field(None, description="Client ID (null = for all clients)")
    is_active: bool = Field(True, description="Is promotion active")


class LoyaltyPromotionCreate(LoyaltyPromotionBase):
    program_id: int


class LoyaltyPromotionUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=200)
    description: Optional[str] = None
    promotion_type: Optional[str] = None
    points_multiplier: Optional[float] = Field(None, ge=0)
    cashback_percent: Optional[float] = Field(None, ge=0)
    bonus_points: Optional[float] = Field(None, ge=0)
    discount_percent: Optional[float] = Field(None, ge=0, le=100)
    service_ids: Optional[str] = None
    min_amount: Optional[float] = Field(None, ge=0)
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    client_id: Optional[int] = None
    is_active: Optional[bool] = None


class LoyaltyPromotionOut(LoyaltyPromotionBase):
    id: int
    program_id: int
    created_at: datetime

    class Config:
        from_attributes = True


# ============ LoyaltyTransaction ============

class LoyaltyTransactionOut(BaseModel):
    id: int
    client_id: int
    business_id: int
    program_id: Optional[int]
    transaction_type: str
    points_amount: float
    cashback_amount: Optional[float]
    points_balance_after: float
    cashback_balance_after: Optional[float]
    visit_id: Optional[int]
    promotion_id: Optional[int]
    level_id: Optional[int]
    description: Optional[str]
    expiry_date: Optional[date]
    created_at: datetime

    class Config:
        from_attributes = True


# ============ Client Loyalty Balance ============

class ClientLoyaltyBalance(BaseModel):
    client_id: int
    business_id: int
    points: float
    cashback: float
    level: Optional[LoyaltyLevelOut] = None
    level_id: Optional[int] = None


# ============ Use Points Request ============

class UsePointsRequest(BaseModel):
    points_to_use: Optional[float] = Field(None, ge=0, description="Points to use")
    cashback_to_use: Optional[float] = Field(None, ge=0, description="Cashback to use")
    visit_id: Optional[int] = Field(None, description="Visit ID if applicable")


# ============ Loyalty Analytics ============

class LoyaltyAnalytics(BaseModel):
    total_points_issued: float
    total_points_spent: float
    total_cashback_issued: float
    total_cashback_spent: float
    active_members: int
    total_transactions: int
    average_points_per_client: float
    redemption_rate: float  # % of issued points that were spent
