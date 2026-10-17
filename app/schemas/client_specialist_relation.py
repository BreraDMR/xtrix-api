from typing import Optional
from pydantic import ConfigDict, BaseModel
from app.schemas.datetimes import UtcDatetime
class ClientSpecialistRelationBase(BaseModel):
    client_id: int
    specialist_id: int
    business_id: Optional[int] = None  
class ClientSpecialistRelationCreate(ClientSpecialistRelationBase):
    pass
class ClientSpecialistRelationOut(ClientSpecialistRelationBase):
    id: int
    business_id: int  
    created_at: UtcDatetime
    model_config = ConfigDict(from_attributes=True)
