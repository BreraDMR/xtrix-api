from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field
class ExpenseCategoryBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    type: str = Field(default="variable", pattern="^(fixed|variable)$")
    color: Optional[str] = Field(None, max_length=7, description="Hex цвет (например, #3B82F6)")
    is_active: bool = Field(default=True)
class ExpenseCategoryCreate(ExpenseCategoryBase):
    business_id: Optional[int] = None  
class ExpenseCategoryUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=100)
    type: Optional[str] = Field(None, pattern="^(fixed|variable)$")
    color: Optional[str] = Field(None, max_length=7)
    is_active: Optional[bool] = None
class ExpenseCategoryOut(ExpenseCategoryBase):
    id: int
    business_id: Optional[int]
    is_system: bool
    created_at: datetime
    class Config:
        from_attributes = True
