from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field
class ProductUsageBase(BaseModel):
    product_id: int
    quantity: float = Field(..., gt=0)
    visit_id: Optional[int] = None
    client_id: Optional[int] = None
    notes: Optional[str] = Field(None, max_length=5000)
class ProductUsageCreate(ProductUsageBase):
    business_id: int
    used_by_id: Optional[int] = None
class ProductUsageOut(ProductUsageBase):
    id: int
    business_id: int
    used_by_id: Optional[int] = None
    usage_date: datetime
    created_at: datetime
    class Config:
        from_attributes = True
