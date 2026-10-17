from typing import Optional, List
from pydantic import BaseModel, Field
from app.schemas.limits import Minutes, Money, Name, ShortText
class ServiceBase(BaseModel):
    name: Name
    category: Optional[ShortText] = Field(None, description="Category of service: beauty, wellness, healthcare. If not provided, will be determined from specialist type.")
    duration_minutes: Optional[Minutes] = None
    price: Optional[Money] = None
    is_active: Optional[bool] = True
class ServiceCreate(ServiceBase):
    specialist_id: int  
    specialist_ids: Optional[List[int]] = Field(None, description="Список ID специалистов, которые могут предоставлять эту услугу")
class ServiceUpdate(BaseModel):
    name: Optional[Name] = None
    category: Optional[ShortText] = None
    duration_minutes: Optional[Minutes] = None
    price: Optional[Money] = None
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
