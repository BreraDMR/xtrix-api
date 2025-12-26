from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class ClientSpecialistRelationBase(BaseModel):
    client_id: int
    specialist_id: int
    business_id: Optional[int] = None  # Опционально для обратной совместимости, но рекомендуется всегда указывать


class ClientSpecialistRelationCreate(ClientSpecialistRelationBase):
    pass


class ClientSpecialistRelationOut(ClientSpecialistRelationBase):
    id: int
    business_id: int  # Обязательное поле в выводе
    created_at: datetime

    class Config:
        from_attributes = True


