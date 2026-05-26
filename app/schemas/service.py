from typing import Optional, List
from pydantic import BaseModel, Field
class ServiceBase(BaseModel):
    name: str = Field(..., max_length=255)
    category: Optional[str] = Field(None, description="Category of service: beauty, wellness, healthcare. If not provided, will be determined from specialist type.")
    duration_minutes: Optional[int] = None
    price: Optional[float] = None
    is_active: Optional[bool] = True
class ServiceCreate(ServiceBase):
    specialist_id: int  
    specialist_ids: Optional[List[int]] = Field(None, description="Список ID специалистов, которые могут предоставлять эту услугу")
class ServiceUpdate(BaseModel):
    name: Optional[str] = None
    category: Optional[str] = None
    duration_minutes: Optional[int] = None
    price: Optional[float] = None
    is_active: Optional[bool] = None
    specialist_id: Optional[int] = None
    specialist_ids: Optional[List[int]] = Field(None, description="Список ID специалистов, которые могут предоставлять эту услугу")
class ServiceOut(ServiceBase):
    id: int
    specialist_id: int
    specialist_ids: Optional[List[int]] = Field(None, description="Список ID всех специалистов, которые могут предоставлять эту услугу")
    specialists: Optional[List[dict]] = Field(None, description="Информация о специалистах")
    class Config:
        from_attributes = True
