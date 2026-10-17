from typing import Optional, List
from pydantic import ConfigDict, BaseModel, Field
from app.schemas.limits import Minutes, Money, Name, ShortText
class ServiceBase(BaseModel):
    name: Name
    category: Optional[ShortText] = Field(None, description="Category of service: beauty, wellness, healthcare. If not provided, will be determined from specialist type.")
    duration_minutes: Optional[Minutes] = None
    price: Optional[Money] = None
    is_active: Optional[bool] = True
class ServiceCreate(ServiceBase):
    specialist_id: int  
    specialist_ids: Optional[List[int]] = Field(None, description="IDs of specialists who provide this service")
class ServiceUpdate(BaseModel):
    name: Optional[Name] = None
    category: Optional[ShortText] = None
    duration_minutes: Optional[Minutes] = None
    price: Optional[Money] = None
    is_active: Optional[bool] = None
    specialist_id: Optional[int] = None
    specialist_ids: Optional[List[int]] = Field(None, description="IDs of specialists who provide this service")
class ServiceOut(ServiceBase):
    id: int
    specialist_id: int
    specialist_ids: Optional[List[int]] = Field(None, description="IDs of all specialists who provide this service")
    specialists: Optional[List[dict]] = Field(None, description="Specialists' info")
    model_config = ConfigDict(from_attributes=True)
