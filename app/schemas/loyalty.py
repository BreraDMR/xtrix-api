from datetime import date
from typing import Optional, List
from app.schemas.limits import HexColor, PromotionType, LongText, LoyaltyProgramType, Money, Multiplier, Percent, Points, ShortText
from pydantic import ConfigDict, BaseModel, Field, model_validator
from app.schemas.datetimes import UtcDatetime
class LoyaltyProgramBase(BaseModel):
    program_type: LoyaltyProgramType
    points_per_currency: Points = Field(1.0, description="Points per currency unit")
    points_name: Optional[ShortText] = Field("points", description="Name of points, e.g. stars")
    currency_per_point: Optional[Money] = Field(None, description="Currency value per point")
    cashback_percent: Optional[float] = Field(0.0, ge=0, le=100, description="Cashback percentage")
    use_levels: bool = Field(False, description="Enable loyalty levels")
    points_expiry_days: Optional[int] = Field(None, ge=1, le=36500, description="Points expiry in days (null = no expiry)")
    birthday_bonus_enabled: bool = Field(False, description="Enable birthday bonuses")
    birthday_bonus_points: Optional[Points] = Field(None, description="Birthday bonus points")
    birthday_bonus_percent: Optional[Percent] = Field(None, description="Birthday bonus cashback percent")
    is_active: bool = Field(True, description="Is program active")
    max_bonus_payment_percent: Optional[float] = Field(100.0, ge=0, le=100, description="Max % of check that can be paid with bonuses")
class LoyaltyProgramCreate(LoyaltyProgramBase):
    business_id: int
class LoyaltyProgramUpdate(BaseModel):
    program_type: Optional[LoyaltyProgramType] = None
    points_per_currency: Optional[Points] = None
    points_name: Optional[ShortText] = None
    currency_per_point: Optional[Money] = None
    cashback_percent: Optional[float] = Field(None, ge=0, le=100)
    use_levels: Optional[bool] = None
    points_expiry_days: Optional[int] = Field(None, ge=1, le=36500)
    birthday_bonus_enabled: Optional[bool] = None
    birthday_bonus_points: Optional[Points] = None
    birthday_bonus_percent: Optional[Percent] = None
    is_active: Optional[bool] = None
    max_bonus_payment_percent: Optional[float] = Field(None, ge=0, le=100)
class LoyaltyProgramOut(LoyaltyProgramBase):
    id: int
    business_id: int
    created_at: UtcDatetime
    updated_at: UtcDatetime
    model_config = ConfigDict(from_attributes=True)
class LoyaltyLevelBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    level_number: int = Field(..., ge=1, description="Level number (1, 2, 3...)")
    min_spending: Money = Field(..., description="Minimum spending for this level")
    points_multiplier: Optional[Multiplier] = Field(1.0, description="Points multiplier")
    cashback_multiplier: Optional[Multiplier] = Field(1.0, description="Cashback multiplier")
    benefits: Optional[LongText] = Field(None, description="Benefits description (JSON)")
    color: Optional[HexColor] = Field(None, description="Color for UI, #rrggbb")
class LoyaltyLevelCreate(LoyaltyLevelBase):
    program_id: int
class LoyaltyLevelUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=100)
    level_number: Optional[int] = Field(None, ge=1)
    min_spending: Optional[Money] = None
    points_multiplier: Optional[Multiplier] = None
    cashback_multiplier: Optional[Multiplier] = None
    benefits: Optional[LongText] = None
    color: Optional[HexColor] = None
class LoyaltyLevelOut(LoyaltyLevelBase):
    id: int
    program_id: int
    created_at: UtcDatetime
    model_config = ConfigDict(from_attributes=True)
class LoyaltyPromotionBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    description: Optional[LongText] = None
    promotion_type: PromotionType
    points_multiplier: Optional[Multiplier] = None
    cashback_percent: Optional[Percent] = None
    bonus_points: Optional[Points] = None
    discount_percent: Optional[Percent] = None
    service_ids: Optional[str] = Field(None, max_length=5000, description="JSON array of service IDs (null = all services)")
    min_amount: Optional[Money] = None
    start_date: date = Field(..., description="Promotion start date")
    end_date: date = Field(..., description="Promotion end date")
    client_id: Optional[int] = Field(None, description="Client ID (null = for all clients)")
    is_active: bool = Field(True, description="Is promotion active")
class LoyaltyPromotionCreate(LoyaltyPromotionBase):
    program_id: int
    @model_validator(mode="after")
    def ends_after_start(self):
        if self.end_date < self.start_date:
            raise ValueError("end_date must not be before start_date")
        return self
class LoyaltyPromotionUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=200)
    description: Optional[LongText] = None
    promotion_type: Optional[PromotionType] = None
    points_multiplier: Optional[Multiplier] = None
    cashback_percent: Optional[Percent] = None
    bonus_points: Optional[Points] = None
    discount_percent: Optional[Percent] = None
    service_ids: Optional[str] = Field(None, max_length=5000)
    min_amount: Optional[Money] = None
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    client_id: Optional[int] = None
    is_active: Optional[bool] = None
class LoyaltyPromotionOut(LoyaltyPromotionBase):
    id: int
    program_id: int
    created_at: UtcDatetime
    model_config = ConfigDict(from_attributes=True)
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
    created_at: UtcDatetime
    model_config = ConfigDict(from_attributes=True)
class ClientLoyaltyBalance(BaseModel):
    client_id: int
    business_id: int
    points: float
    cashback: float
    level: Optional[LoyaltyLevelOut] = None
    level_id: Optional[int] = None
class UsePointsRequest(BaseModel):
    points_to_use: Optional[float] = Field(None, ge=0, description="Points to use")
    cashback_to_use: Optional[float] = Field(None, ge=0, description="Cashback to use")
    visit_id: Optional[int] = Field(None, description="Visit ID if applicable")
class LoyaltyStatisticsResponse(BaseModel):
    total_participants: int
    total_points_earned: float
    total_points_spent: float
    total_cashback_earned: float
    total_cashback_spent: float
    active_points_balance: float
    active_cashback_balance: float
    points_redemption_rate: float
    cashback_redemption_rate: float
class LevelDistributionItem(BaseModel):
    level_id: Optional[int]
    level_name: str
    level_number: int
    client_count: int
    min_spending: float
    color: Optional[str] = None
class LevelDistributionResponse(BaseModel):
    distribution: List[LevelDistributionItem]
class TopClientItem(BaseModel):
    client_id: int
    client_name: str
    points: float
    cashback: float
    level_id: Optional[int] = None
class TopClientsResponse(BaseModel):
    clients: List[TopClientItem]
class PromotionEffectivenessItem(BaseModel):
    promotion_id: int
    promotion_name: str
    promotion_type: str
    start_date: str
    end_date: str
    is_active: bool
    usage_count: int
    unique_clients: int
    total_points_awarded: float
    total_cashback_awarded: float
class PromotionEffectivenessResponse(BaseModel):
    promotions: List[PromotionEffectivenessItem]
class PointsUsageTrendItem(BaseModel):
    period: str
    points_earned: float
    points_spent: float
    cashback_earned: float
    cashback_spent: float
    transaction_count: int
class PointsUsageTrendsResponse(BaseModel):
    trends: List[PointsUsageTrendItem]
class LoyaltyAnalytics(BaseModel):
    total_points_issued: float
    total_points_spent: float
    total_cashback_issued: float
    total_cashback_spent: float
    active_members: int
    total_transactions: int
    average_points_per_client: float
    redemption_rate: float  
