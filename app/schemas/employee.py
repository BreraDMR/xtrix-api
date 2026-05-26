from typing import Optional, Dict, Any, List
from datetime import datetime
from pydantic import BaseModel, Field, field_validator
class EmployeePermissions(BaseModel):
    can_create_visits: bool = Field(default=True, description="Может создавать визиты")
    can_edit_clients: bool = Field(default=True, description="Может редактировать клиентов")
    can_view_financials: bool = Field(default=False, description="Может просматривать финансовую информацию")
    can_manage_services: bool = Field(default=False, description="Может управлять услугами")
    assigned_client_ids: Optional[List[int]] = Field(
        default=None, 
        description="Список ID клиентов, доступных сотруднику. None или [] = все клиенты бизнеса"
    )
class EmployeeBase(BaseModel):
    employee_user_id: int = Field(..., description="ID пользователя-сотрудника")
    position: Optional[str] = Field(None, max_length=100, description="Должность: 'массажист', 'администратор' и т.д.")
    permissions: Optional[Dict[str, Any]] = Field(
        None, 
        description="Права доступа (JSON). Если не указано, используются значения по умолчанию."
    )
    commission_percentage: Optional[float] = Field(0.0, ge=0.0, le=100.0, description="% комиссии с каждого визита")
    base_salary: Optional[float] = Field(0.0, ge=0.0, description="Фиксированная часть зарплаты")
    is_active: bool = Field(default=True, description="Активен ли сотрудник")
    notes: Optional[str] = Field(None, description="Заметки о сотруднике")
    @field_validator('permissions')
    @classmethod
    def validate_permissions(cls, v):
        if v is not None and not isinstance(v, dict):
            raise ValueError("permissions must be a dictionary")
        return v
class EmployeeCreate(EmployeeBase):
    business_owner_id: Optional[int] = Field(None, description="ID владельца бизнеса (опционально, используется для admin)")
class EmployeeCreateWithUser(BaseModel):
    full_name: str = Field(..., min_length=1, max_length=255, description="Полное имя сотрудника")
    phone: Optional[str] = Field(None, description="Телефон сотрудника")
    email: Optional[str] = Field(None, description="Email сотрудника")
    specialist_type: Optional[str] = Field(None, description="Тип специалиста (beauty, wellness, healthcare, other)")
    business_id: Optional[int] = Field(None, description="ID бизнеса для привязки (автоматически берется из админа)")
    position: Optional[str] = Field(None, max_length=100, description="Должность")
    permissions: Optional[Dict[str, Any]] = Field(None, description="Права доступа")
    commission_percentage: Optional[float] = Field(0.0, ge=0.0, le=100.0)
    base_salary: Optional[float] = Field(0.0, ge=0.0)
    notes: Optional[str] = Field(None, description="Заметки")
class EmployeeUpdate(BaseModel):
    position: Optional[str] = Field(None, max_length=100)
    permissions: Optional[Dict[str, Any]] = Field(None)
    commission_percentage: Optional[float] = Field(None, ge=0.0, le=100.0)
    base_salary: Optional[float] = Field(None, ge=0.0)
    is_active: Optional[bool] = None
    notes: Optional[str] = None
class EmployeeOut(EmployeeBase):
    id: int
    business_owner_id: int
    hired_at: Optional[datetime] = None
    fired_at: Optional[datetime] = None
    employee_user: Optional[Dict[str, Any]] = None  
    class Config:
        from_attributes = True
class EmployeeCreateResponse(EmployeeOut):
    generated_password: str = Field(..., description="Сгенерированный пароль для сотрудника")
    user_id: int = Field(..., description="ID созданного пользователя")
