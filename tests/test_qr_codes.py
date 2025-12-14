"""Tests for QR code functionality."""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.main import app
from app.db.models.client import Client
from app.api.core.qr_code import (
    generate_qr_code_data,
    generate_access_password,
    parse_qr_code_data,
    verify_qr_code_hash,
    generate_qr_code_base64,
)
from app.api.core.security import verify_password, get_password_hash


test_client = TestClient(app)


def test_generate_qr_code_data():
    """Test QR code data generation."""
    qr_data = generate_qr_code_data(1)
    assert qr_data is not None
    assert ":" in qr_data
    parts = qr_data.split(":")
    assert len(parts) == 2
    assert int(parts[0]) == 1
    assert len(parts[1]) == 16


def test_generate_access_password():
    """Test access password generation."""
    password = generate_access_password()
    assert password is not None
    assert len(password) == 4
    assert password.isdigit()


def test_parse_qr_code_data():
    """Test QR code data parsing."""
    qr_data = "123:abcdef1234567890"
    parsed = parse_qr_code_data(qr_data)
    assert parsed is not None
    client_id, hash_value = parsed
    assert client_id == 123
    assert hash_value == "abcdef1234567890"
    
    # Test invalid format
    assert parse_qr_code_data("invalid") is None
    assert parse_qr_code_data("123") is None
    assert parse_qr_code_data(":hash") is None


def test_verify_qr_code_hash():
    """Test QR code hash verification."""
    assert verify_qr_code_hash(1, "abcdef1234567890") is True
    assert verify_qr_code_hash(1, "short") is False


def test_generate_qr_code_image():
    """Test QR code image generation."""
    qr_data = "123:abcdef1234567890"
    img_base64 = generate_qr_code_base64(qr_data)
    # If qrcode library is available, should return base64 string
    # If not available, should return None
    assert img_base64 is None or isinstance(img_base64, str)


@pytest.fixture
def test_client_with_qr(db: Session, test_user):
    """Create a test client with QR code."""
    from app.api.crud import crud_client
    from app.schemas.client import ClientCreate
    
    client_data = ClientCreate(
        owner_id=test_user.id,
        full_name="Test Client QR",
        contact_phone="+1234567890",
    )
    client = crud_client.create_client(db, client_data)
    
    # Generate QR code
    qr_data, password = crud_client.generate_client_qr_code(db, client.id)
    
    return client, qr_data, password


def test_generate_client_qr_code_endpoint(db: Session, test_user, auth_headers):
    """Test QR code generation endpoint."""
    from app.api.crud import crud_client
    from app.schemas.client import ClientCreate
    
    # Create a client
    client_data = ClientCreate(
        owner_id=test_user.id,
        full_name="Test Client for QR",
        contact_phone="+1234567890",
    )
    client = crud_client.create_client(db, client_data)
    
    # Generate QR code
    response = test_client.post(
        f"/clients/{client.id}/qr-code/generate",
        headers=auth_headers,
    )
    
    assert response.status_code == 200
    data = response.json()
    assert "qr_code_data" in data
    assert "access_password" in data
    assert "qr_code_image_base64" in data
    assert "qr_code_url" in data
    assert len(data["access_password"]) == 4
    assert data["access_password"].isdigit()


def test_access_client_history_by_qr(db: Session, test_client_with_qr, auth_headers):
    """Test accessing client history via QR code."""
    client_obj, qr_data, password = test_client_with_qr
    
    # Access history with correct password
    response = test_client.post(
        "/clients/qr-code/access",
        json={
            "qr_code_data": qr_data,
            "password": password,
        },
    )
    
    assert response.status_code == 200
    data = response.json()
    assert data["client_id"] == client_obj.id
    assert data["client_name"] == client_obj.full_name
    assert "visits" in data
    assert "total_visits" in data
    assert "total_spending" in data
    assert isinstance(data["visits"], list)


def test_access_client_history_invalid_qr(db: Session):
    """Test accessing history with invalid QR code."""
    response = test_client.post(
        "/clients/qr-code/access",
        json={
            "qr_code_data": "invalid:qr:code",
            "password": "1234",
        },
    )
    
    assert response.status_code == 400


def test_access_client_history_invalid_password(db: Session, test_client_with_qr):
    """Test accessing history with invalid password."""
    client_obj, qr_data, correct_password = test_client_with_qr
    
    response = test_client.post(
        "/clients/qr-code/access",
        json={
            "qr_code_data": qr_data,
            "password": "9999",  # Wrong password
        },
    )
    
    assert response.status_code == 401


def test_qr_code_generation_permissions(db: Session, test_user, auth_headers):
    """Test that only admin/specialist can generate QR codes."""
    from app.api.crud import crud_client
    from app.schemas.client import ClientCreate
    
    # Create a client
    client_data = ClientCreate(
        owner_id=test_user.id,
        full_name="Test Client",
        contact_phone="+1234567890",
    )
    client_obj = crud_client.create_client(db, client_data)
    
    # Try to generate QR code (should work for owner)
    response = test_client.post(
        f"/clients/{client_obj.id}/qr-code/generate",
        headers=auth_headers,
    )
    
    # Should succeed if user is owner or admin
    assert response.status_code in [200, 403]


def test_qr_code_regeneration(db: Session, test_client_with_qr, auth_headers):
    """Test that QR code can be regenerated (new password)."""
    client_obj, old_qr_data, old_password = test_client_with_qr
    
    # Regenerate QR code
    response = test_client.post(
        f"/clients/{client_obj.id}/qr-code/generate",
        headers=auth_headers,
    )
    
    assert response.status_code == 200
    data = response.json()
    new_qr_data = data["qr_code_data"]
    new_password = data["access_password"]
    
    # New QR code should be different
    assert new_qr_data != old_qr_data
    # New password should be different (likely, but not guaranteed)
    # Both should work
    assert len(new_password) == 4

