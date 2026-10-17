from typing import Optional, Dict, Any, List
from datetime import datetime
from app.schemas.limits import MAX_MONEY
from pydantic import ConfigDict, BaseModel, Field, field_validator
class EmployeePermissions(BaseModel):
    can_create_visits: bool = Field(default=True, description="Can create visits")
    can_edit_clients: bool = Field(default=True, description="Can edit clients")
    can_view_financials: bool = Field(default=False, description="Can view financials")
    can_manage_services: bool = Field(default=False, description="Can manage services")
    assigned_client_ids: Optional[List[int]] = Field(
        default=None, 
        description="Client IDs the employee may access. None or [] = all business clients"
    )
class EmployeeBase(BaseModel):
    employee_user_id: int = Field(..., description="Employee's user ID")
    position: Optional[str] = Field(None, max_length=100, description="Position, e.g. 'masseur', 'receptionist'")
    permissions: Optional[Dict[str, Any]] = Field(
        None, 
        description="Permissions (JSON). Defaults apply when omitted."
    )
    commission_percentage: Optional[float] = Field(0.0, ge=0.0, le=100.0, description="Commission % per visit")
    base_salary: Optional[float] = Field(0.0, ge=0.0, le=MAX_MONEY, description="Fixed part of the salary")
    is_active: bool = Field(default=True, description="Is the employee active")
    notes: Optional[str] = Field(None, description="Notes about the employee")
    @field_validator('permissions')
    @classmethod
    def validate_permissions(cls, v):
        if v is not None and not isinstance(v, dict):
            raise ValueError("permissions must be a dictionary")
        return v
class EmployeeCreate(EmployeeBase):
    business_owner_id: Optional[int] = Field(None, description="Business owner ID (optional, admin only)")
class EmployeeCreateWithUser(BaseModel):
    full_name: str = Field(..., min_length=1, max_length=255, description="Employee's full name")
    phone: Optional[str] = Field(None, max_length=50, description="Employee phone")
    email: Optional[str] = Field(None, max_length=255, description="Employee email")
    specialist_type: Optional[str] = Field(None, max_length=64, description="Specialist type")
    business_id: Optional[int] = Field(None, description="Business to attach to (taken from the caller by default)")
    position: Optional[str] = Field(None, max_length=100, description="Position")
    permissions: Optional[Dict[str, Any]] = Field(None, description="Permissions")
    commission_percentage: Optional[float] = Field(0.0, ge=0.0, le=100.0)
    base_salary: Optional[float] = Field(0.0, ge=0.0, le=MAX_MONEY)
    notes: Optional[str] = Field(None, max_length=5000, description="Notes")
class EmployeeUpdate(BaseModel):
    position: Optional[str] = Field(None, max_length=100)
    permissions: Optional[Dict[str, Any]] = Field(None)
    commission_percentage: Optional[float] = Field(None, ge=0.0, le=100.0)
    base_salary: Optional[float] = Field(None, ge=0.0, le=MAX_MONEY)
    is_active: Optional[bool] = None
    notes: Optional[str] = Field(None, max_length=5000)
class EmployeeOut(EmployeeBase):
    id: int
    business_owner_id: int
    hired_at: Optional[datetime] = None
    fired_at: Optional[datetime] = None
    employee_user: Optional[Dict[str, Any]] = None  
    model_config = ConfigDict(from_attributes=True)
class EmployeeCreateResponse(EmployeeOut):
    generated_password: str = Field(..., description="Generated password for the employee")
    user_id: int = Field(..., description="ID of the created user")
