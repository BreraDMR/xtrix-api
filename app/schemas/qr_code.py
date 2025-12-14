"""QR code related schemas."""
from pydantic import BaseModel, Field


class QRCodeGenerateResponse(BaseModel):
    """Response for QR code generation."""
    qr_code_data: str = Field(..., description="QR code data string")
    qr_code_image_base64: str | None = Field(None, description="QR code image as base64 (if available)")
    access_password: str = Field(..., description="Access password for client (4 digits)")
    qr_code_url: str = Field(..., description="URL to access client history")


class QRCodeAccessRequest(BaseModel):
    """Request to access client history via QR code."""
    qr_code_data: str = Field(..., description="QR code data string")
    password: str = Field(..., min_length=4, max_length=6, description="Access password")


class ClientHistoryResponse(BaseModel):
    """Response with client history."""
    client_id: int
    client_name: str
    visits: list[dict]
    total_visits: int
    total_spending: float
    last_visit_date: str | None









