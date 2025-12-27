from typing import Optional, Dict, Any, List
from datetime import datetime

from pydantic import BaseModel, Field, field_validator


class EmployeePermissions(BaseModel):
    """Модель прав доступа сотрудника."""
    can_create_visits: bool = Field(default=True, description="Может создавать визиты")
    can_edit_clients: bool = Field(default=True, description="Может редактировать клиентов")
    can_view_financials: bool = Field(default=False, description="Может просматривать финансовую информацию")
    can_manage_services: bool = Field(default=False, description="Может управлять услугами")
    assigned_client_ids: Optional[List[int]] = Field(
        default=None, 
        description="Список ID клиентов, доступных сотруднику. None или [] = все клиенты бизнеса"
    )


class EmployeeBase(BaseModel):
    """Базовая модель сотрудника."""
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
    """Модель для создания сотрудника."""
    pass


class EmployeeUpdate(BaseModel):
    """Модель для обновления сотрудника."""
    position: Optional[str] = Field(None, max_length=100)
    permissions: Optional[Dict[str, Any]] = Field(None)
    commission_percentage: Optional[float] = Field(None, ge=0.0, le=100.0)
    base_salary: Optional[float] = Field(None, ge=0.0)
    is_active: Optional[bool] = None
    notes: Optional[str] = None


class EmployeeOut(EmployeeBase):
    """Модель для вывода информации о сотруднике."""
    id: int
    business_owner_id: int
    hired_at: datetime
    fired_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class EmployeeWithUser(EmployeeOut):
    """Модель сотрудника с информацией о пользователе."""
    employee_user: Optional[Dict[str, Any]] = Field(None, description="Информация о пользователе-сотруднике")

