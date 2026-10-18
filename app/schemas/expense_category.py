from typing import Optional
from pydantic import ConfigDict, BaseModel, Field
from app.schemas.limits import HexColor
from app.schemas.datetimes import UtcDatetime
from app.schemas.base import InputModel
class ExpenseCategoryBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    type: str = Field(default="variable", pattern="^(fixed|variable)$")
    color: Optional[HexColor] = Field(None, description="Hex color, e.g. #3B82F6")
    is_active: bool = Field(default=True)
class ExpenseCategoryCreate(InputModel, ExpenseCategoryBase):
    business_id: Optional[int] = None  
class ExpenseCategoryUpdate(InputModel):
    name: Optional[str] = Field(None, min_length=1, max_length=100)
    type: Optional[str] = Field(None, pattern="^(fixed|variable)$")
    color: Optional[HexColor] = None
    is_active: Optional[bool] = None
class ExpenseCategoryOut(ExpenseCategoryBase):
    id: int
    business_id: Optional[int]
    code: Optional[str] = Field(None, description="Set on system categories: rent, salaries, stock_purchase, "
                                                  "marketing, utilities, subscriptions, other")
    is_system: bool
    created_at: UtcDatetime
    model_config = ConfigDict(from_attributes=True)
