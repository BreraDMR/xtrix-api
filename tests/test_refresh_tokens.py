import pytest
from fastapi.testclient import TestClient
from datetime import datetime, timedelta
from app.main import app
from app.api.core.security import create_refresh_token, verify_refresh_token
from app.api.crud import crud_user, crud_refresh_token
from app.schemas.user import UserCreate
from app.db.session import SessionLocal
client = TestClient(app)
@pytest.fixture
def test_user(db):
    user_data = UserCreate(
        full_name="Test User",
        phone="+1234567890",
        email="test@example.com",
        password="testpassword123",
        role="specialist",
    )
    user = crud_user.create_user(db, user_data)
    yield user
    db.delete(user)
    db.commit()
@pytest.fixture
def db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
def test_login_returns_both_tokens(test_user, db):
    response = client.post(
        "/users/login",
        json={"email": "test@example.com", "password": "testpassword123"},
    )
    assert response.status_code == 200
    data = response.json()
    assert "access_token" in data
    assert "refresh_token" in data
    assert data["token_type"] == "bearer"
    db_token = crud_refresh_token.get_refresh_token(db, data["refresh_token"])
    assert db_token is not None
    assert db_token.user_id == test_user.id
    assert db_token.is_revoked == False
def test_refresh_token_endpoint(test_user, db):
    login_response = client.post(
        "/users/login",
        json={"email": "test@example.com", "password": "testpassword123"},
    )
    assert login_response.status_code == 200
    refresh_token = login_response.json()["refresh_token"]
    refresh_response = client.post(
        "/users/refresh",
        json={"refresh_token": refresh_token},
    )
    assert refresh_response.status_code == 200
    data = refresh_response.json()
    assert "access_token" in data
    assert "refresh_token" in data
    assert data["token_type"] == "bearer"
    old_token = crud_refresh_token.get_refresh_token(db, refresh_token)
    assert old_token is None or old_token.is_revoked == True
    new_token = crud_refresh_token.get_refresh_token(db, data["refresh_token"])
    assert new_token is not None
    assert new_token.is_revoked == False
def test_refresh_token_invalid_token():
    response = client.post(
        "/users/refresh",
        json={"refresh_token": "invalid_token"},
    )
    assert response.status_code == 401
def test_refresh_token_expired_token(test_user, db):
    expired_time = datetime.utcnow() - timedelta(days=1)
    token_str, _ = create_refresh_token({"sub": str(test_user.id), "role": test_user.role})
    from app.schemas.refresh_token import RefreshTokenCreate
    token_data = RefreshTokenCreate(
        token=token_str,
        user_id=test_user.id,
        expires_at=expired_time,
    )
    crud_refresh_token.create_refresh_token(db, token_data)
    response = client.post(
        "/users/refresh",
        json={"refresh_token": token_str},
    )
    assert response.status_code == 401
def test_logout_revokes_token(test_user, db):
    login_response = client.post(
        "/users/login",
        json={"email": "test@example.com", "password": "testpassword123"},
    )
    assert login_response.status_code == 200
    access_token = login_response.json()["access_token"]
    refresh_token = login_response.json()["refresh_token"]
    logout_response = client.post(
        "/users/logout",
        json={"refresh_token": refresh_token},
        headers={"Authorization": f"Bearer {access_token}"},
    )
    assert logout_response.status_code == 200
    db_token = crud_refresh_token.get_refresh_token(db, refresh_token)
    assert db_token is None or db_token.is_revoked == True
def test_logout_all_revokes_all_tokens(test_user, db):
    tokens = []
    for _ in range(3):
        login_response = client.post(
            "/users/login",
            json={"email": "test@example.com", "password": "testpassword123"},
        )
        assert login_response.status_code == 200
        tokens.append(login_response.json())
    access_token = tokens[0]["access_token"]
    logout_response = client.post(
        "/users/logout-all",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    assert logout_response.status_code == 200
    assert logout_response.json()["revoked_count"] == 3
    for token_data in tokens:
        db_token = crud_refresh_token.get_refresh_token(db, token_data["refresh_token"])
        assert db_token is None or db_token.is_revoked == True
def test_refresh_token_after_logout(test_user, db):
    login_response = client.post(
        "/users/login",
        json={"email": "test@example.com", "password": "testpassword123"},
    )
    assert login_response.status_code == 200
    access_token = login_response.json()["access_token"]
    refresh_token = login_response.json()["refresh_token"]
    logout_response = client.post(
        "/users/logout",
        json={"refresh_token": refresh_token},
        headers={"Authorization": f"Bearer {access_token}"},
    )
    assert logout_response.status_code == 200
    refresh_response = client.post(
        "/users/refresh",
        json={"refresh_token": refresh_token},
    )
    assert refresh_response.status_code == 401
def test_refresh_token_rotation(test_user, db):
    login_response = client.post(
        "/users/login",
        json={"email": "test@example.com", "password": "testpassword123"},
    )
    assert login_response.status_code == 200
    refresh_token_1 = login_response.json()["refresh_token"]
    refresh_response_1 = client.post(
        "/users/refresh",
        json={"refresh_token": refresh_token_1},
    )
    assert refresh_response_1.status_code == 200
    refresh_token_2 = refresh_response_1.json()["refresh_token"]
    assert refresh_token_1 != refresh_token_2
    refresh_response_2 = client.post(
        "/users/refresh",
        json={"refresh_token": refresh_token_2},
    )
    assert refresh_response_2.status_code == 200
    refresh_token_3 = refresh_response_2.json()["refresh_token"]
    assert refresh_token_2 != refresh_token_3
    assert crud_refresh_token.get_refresh_token(db, refresh_token_1) is None
    assert crud_refresh_token.get_refresh_token(db, refresh_token_2) is None
    assert crud_refresh_token.get_refresh_token(db, refresh_token_3) is not None
