from typing import Optional, List
from pydantic import ConfigDict, BaseModel, Field
from app.schemas.limits import Minutes, Money, Name, ShortText
from app.schemas.base import InputModel
class ServiceBase(BaseModel):
    name: Name
    category: Optional[ShortText] = Field(None, description="Category of service: beauty, wellness, healthcare. If not provided, will be determined from specialist type.")
    duration_minutes: Optional[Minutes] = None
    price: Optional[Money] = None
    is_active: Optional[bool] = True
class ServiceCreate(InputModel, ServiceBase):
    specialist_id: int  
    specialist_ids: Optional[List[int]] = Field(None, description="IDs of specialists who provide this service")
class ServiceUpdate(InputModel):
    name: Optional[Name] = None
    category: Optional[ShortText] = None
    duration_minutes: Optional[Minutes] = None
    price: Optional[Money] = None
    is_active: Optional[bool] = None
    specialist_id: Optional[int] = None
    specialist_ids: Optional[List[int]] = Field(None, description="IDs of specialists who provide this service")
class SpecialistBrief(BaseModel):
    id: int
    full_name: str
    model_config = ConfigDict(from_attributes=True)
class ServiceSpecialistOut(SpecialistBrief):
    email: Optional[str] = None
    phone: Optional[str] = None
class ServiceOut(ServiceBase):
    id: int
    specialist_id: int
    specialist: Optional[SpecialistBrief] = Field(None, description="The specialist the service belongs to (specialist_id), just id and name")
    specialist_ids: Optional[List[int]] = Field(None, description="IDs of all specialists who provide this service")
    specialists: Optional[List[ServiceSpecialistOut]] = Field(None, description="Specialists' info")
    model_config = ConfigDict(from_attributes=True)
