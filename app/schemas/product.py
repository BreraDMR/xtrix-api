from datetime import datetime
from typing import Optional
from app.schemas.limits import MAX_MONEY
from pydantic import ConfigDict, BaseModel, Field
class ProductBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    description: Optional[str] = Field(None, max_length=5000)
    unit: str = Field(default="шт", max_length=50)
    quantity: float = Field(default=0.0, ge=0, le=MAX_MONEY)
    min_quantity: float = Field(default=0.0, ge=0, le=MAX_MONEY)
    price_per_unit: Optional[float] = Field(None, ge=0, le=MAX_MONEY)
    category: Optional[str] = Field(None, max_length=100)
    barcode: Optional[str] = Field(None, max_length=100)
    supplier: Optional[str] = Field(None, max_length=255)
    location: Optional[str] = Field(None, max_length=255)
    is_active: bool = Field(default=True)
class ProductCreate(ProductBase):
    business_id: int
class ProductUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    description: Optional[str] = Field(None, max_length=5000)
    unit: Optional[str] = Field(None, max_length=50)
    quantity: Optional[float] = Field(None, ge=0, le=MAX_MONEY)
    min_quantity: Optional[float] = Field(None, ge=0, le=MAX_MONEY)
    price_per_unit: Optional[float] = Field(None, ge=0, le=MAX_MONEY)
    category: Optional[str] = Field(None, max_length=100)
    barcode: Optional[str] = Field(None, max_length=100)
    supplier: Optional[str] = Field(None, max_length=255)
    location: Optional[str] = Field(None, max_length=255)
    is_active: Optional[bool] = None
class ProductOut(ProductBase):
    id: int
    business_id: int
    created_at: datetime
    updated_at: datetime
    model_config = ConfigDict(from_attributes=True)
class ProductStockOut(BaseModel):
    id: int
    name: str
    quantity: float
    min_quantity: float
    unit: str
    is_low_stock: bool  
    model_config = ConfigDict(from_attributes=True)
