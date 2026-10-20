from typing import Annotated, Optional, List
from pydantic import ConfigDict, BaseModel, Field
from app.schemas.limits import LongText, Minutes, Money, Name, ShortText
from app.schemas.base import InputModel

BufferMinutes = Annotated[int, Field(ge=0, le=24 * 60)]

class ServiceBase(BaseModel):
    name: Name
    category: Optional[ShortText] = Field(None, description="Category of service: beauty, wellness, healthcare. If not provided, will be determined from specialist type.")
    description: Optional[LongText] = None
    duration_minutes: Optional[Minutes] = None
    buffer_before_minutes: BufferMinutes = Field(0, description="Reserved time before the visit, minutes")
    buffer_after_minutes: BufferMinutes = Field(0, description="Reserved time after the visit, minutes")
    price: Optional[Money] = None
    is_active: Optional[bool] = True
class ServiceCreate(InputModel, ServiceBase):
    specialist_id: int  
    specialist_ids: Optional[List[int]] = Field(None, description="IDs of specialists who provide this service")
class ServiceUpdate(InputModel):
    name: Optional[Name] = None
    category: Optional[ShortText] = None
    description: Optional[LongText] = None
    duration_minutes: Optional[Minutes] = None
    buffer_before_minutes: BufferMinutes = 0
    buffer_after_minutes: BufferMinutes = 0
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
