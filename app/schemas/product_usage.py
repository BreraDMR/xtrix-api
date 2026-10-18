from typing import Optional
from pydantic import ConfigDict, BaseModel, Field
from app.schemas.datetimes import UtcDatetime
from app.schemas.base import InputModel
class ProductUsageBase(BaseModel):
    product_id: int
    quantity: float = Field(..., gt=0)
    visit_id: Optional[int] = None
    client_id: Optional[int] = None
    notes: Optional[str] = Field(None, max_length=5000)
class ProductUsageCreate(InputModel, ProductUsageBase):
    # the business is always the caller's own, a business_id in the body is ignored
    used_by_id: Optional[int] = None
class ProductUsageOut(ProductUsageBase):
    id: int
    business_id: int
    source: Optional[str] = Field(None, description="null for manual write-offs, visit when a visit made it")
    used_by_id: Optional[int] = None
    usage_date: UtcDatetime
    created_at: UtcDatetime
    model_config = ConfigDict(from_attributes=True)
