from typing import Optional

from pydantic import BaseModel, Field


class ServiceBase(BaseModel):
    name: str = Field(..., max_length=255)
    duration_minutes: Optional[int] = None
    price: Optional[float] = None
    is_active: Optional[bool] = True


class ServiceCreate(ServiceBase):
    specialist_id: int


class ServiceUpdate(BaseModel):
    name: Optional[str] = None
    duration_minutes: Optional[int] = None
    price: Optional[float] = None
    is_active: Optional[bool] = None


class ServiceOut(ServiceBase):
    id: int
    specialist_id: int

    class Config:
        from_attributes = True

