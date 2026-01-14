from typing import Optional
from datetime import datetime

from pydantic import BaseModel, Field


class BusinessBase(BaseModel):
    """Базовая модель бизнеса."""
    name: str = Field(..., min_length=1, max_length=255, description="Название бизнеса")
    business_type: str = Field(
        ..., 
        description="Тип бизнеса: salon, clinic, medical, dental, hospital, individual"
    )
    address: Optional[str] = Field(None, description="Адрес бизнеса")
    phone: Optional[str] = Field(None, description="Телефон бизнеса")
    email: Optional[str] = Field(None, description="Email бизнеса")
    description: Optional[str] = Field(None, description="Описание бизнеса")
    currency: str = Field(default="EUR", description="Валюта: EUR, USD, CZK, RUB, UAH")


class BusinessCreate(BusinessBase):
    """Модель для создания бизнеса."""
    pass


class BusinessUpdate(BaseModel):
    """Модель для обновления бизнеса."""
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    business_type: Optional[str] = None
    address: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    description: Optional[str] = None
    currency: Optional[str] = None


class BusinessOut(BusinessBase):
    """Модель для вывода информации о бизнесе."""
    id: int
    created_at: datetime
    
    class Config:
        from_attributes = True

