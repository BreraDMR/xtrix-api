import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from app.main import app
from app.api.core.qr_code import (
    generate_qr_code_data,
    generate_access_password,
    parse_qr_code_data,
    verify_qr_code_hash,
    generate_qr_code_base64,
)
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
def _create_client(headers, full_name="Test Client QR"):
    r = test_client.post("/clients/", json={"full_name": full_name, "contact_phone": "+420777000222"}, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()
@pytest.fixture
def test_client_with_qr(db: Session, auth_headers):
    from app.api.crud import crud_client
    created = _create_client(auth_headers)
    client = crud_client.get_client(db, created["id"])
    qr_data, password = crud_client.generate_client_qr_code(db, client.id)
    return client, qr_data, password
def test_generate_client_qr_code_endpoint(db: Session, auth_headers):
    client = _create_client(auth_headers, "Test Client for QR")
    response = test_client.post(
        f"/clients/{client['id']}/qr-code/generate",
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
def test_qr_code_generation_permissions(db: Session, auth_headers, make_user, headers_for):
    client_obj = _create_client(auth_headers, "Test Client")
    response = test_client.post(
        f"/clients/{client_obj['id']}/qr-code/generate",
        headers=headers_for(make_user("specialist")),
    )
    assert response.status_code == 403
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
def test_qr_pin_guessing_locks_the_qr(db: Session, test_client_with_qr, auth_headers):
    client_obj, qr_data, password = test_client_with_qr
    wrong = "0000" if password != "0000" else "1111"
    access = lambda pin: test_client.post("/clients/qr-code/access", json={"qr_code_data": qr_data, "password": pin})
    for _ in range(4):
        assert access(wrong).status_code == 401
    assert access(password).status_code == 200  # a right PIN resets the counter
    for _ in range(5):
        assert access(wrong).status_code == 401
    r = access(password)
    assert r.status_code == 429  # locked now, even with the right PIN
    # issuing a new QR unlocks
    regen = test_client.post(f"/clients/{client_obj.id}/qr-code/generate", headers=auth_headers).json()
    r = test_client.post("/clients/qr-code/access", json={"qr_code_data": regen["qr_code_data"], "password": regen["access_password"]})
    assert r.status_code == 200
