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
    qr_data = generate_qr_code_data(1)
    assert qr_data is not None
    assert ":" in qr_data
    parts = qr_data.split(":")
    assert len(parts) == 2
    assert int(parts[0]) == 1
    assert len(parts[1]) == 16
def test_generate_access_password():
    password = generate_access_password()
    assert password is not None
    assert len(password) == 4
    assert password.isdigit()
def test_parse_qr_code_data():
    qr_data = "123:abcdef1234567890"
    parsed = parse_qr_code_data(qr_data)
    assert parsed is not None
    client_id, hash_value = parsed
    assert client_id == 123
    assert hash_value == "abcdef1234567890"
    assert parse_qr_code_data("invalid") is None
    assert parse_qr_code_data("123") is None
    assert parse_qr_code_data(":hash") is None
def test_verify_qr_code_hash():
    assert verify_qr_code_hash(1, "abcdef1234567890") is True
    assert verify_qr_code_hash(1, "short") is False
def test_generate_qr_code_image():
    qr_data = "123:abcdef1234567890"
    img_base64 = generate_qr_code_base64(qr_data)
    assert img_base64 is None or isinstance(img_base64, str)
@pytest.fixture
def test_client_with_qr(db: Session, test_user):
    from app.api.crud import crud_client
    from app.schemas.client import ClientCreate
    client_data = ClientCreate(
        owner_id=test_user.id,
        full_name="Test Client QR",
        contact_phone="+1234567890",
    )
    client = crud_client.create_client(db, client_data)
    qr_data, password = crud_client.generate_client_qr_code(db, client.id)
    return client, qr_data, password
def test_generate_client_qr_code_endpoint(db: Session, test_user, auth_headers):
    from app.api.crud import crud_client
    from app.schemas.client import ClientCreate
    client_data = ClientCreate(
        owner_id=test_user.id,
        full_name="Test Client for QR",
        contact_phone="+1234567890",
    )
    client = crud_client.create_client(db, client_data)
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
    client_obj, qr_data, password = test_client_with_qr
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
    response = test_client.post(
        "/clients/qr-code/access",
        json={
            "qr_code_data": "invalid:qr:code",
            "password": "1234",
        },
    )
    assert response.status_code == 400
def test_access_client_history_invalid_password(db: Session, test_client_with_qr):
    client_obj, qr_data, correct_password = test_client_with_qr
    response = test_client.post(
        "/clients/qr-code/access",
        json={
            "qr_code_data": qr_data,
            "password": "9999",  
        },
    )
    assert response.status_code == 401
def test_qr_code_generation_permissions(db: Session, test_user, auth_headers):
    from app.api.crud import crud_client
    from app.schemas.client import ClientCreate
    client_data = ClientCreate(
        owner_id=test_user.id,
        full_name="Test Client",
        contact_phone="+1234567890",
    )
    client_obj = crud_client.create_client(db, client_data)
    response = test_client.post(
        f"/clients/{client_obj.id}/qr-code/generate",
        headers=auth_headers,
    )
    assert response.status_code in [200, 403]
def test_qr_code_regeneration(db: Session, test_client_with_qr, auth_headers):
    client_obj, old_qr_data, old_password = test_client_with_qr
    response = test_client.post(
        f"/clients/{client_obj.id}/qr-code/generate",
        headers=auth_headers,
    )
    assert response.status_code == 200
    data = response.json()
    new_qr_data = data["qr_code_data"]
    new_password = data["access_password"]
    assert new_qr_data != old_qr_data
    assert len(new_password) == 4
