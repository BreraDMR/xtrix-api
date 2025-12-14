"""Tests for refresh token functionality."""
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
    """Create a test user."""
    user_data = UserCreate(
        full_name="Test User",
        phone="+1234567890",
        email="test@example.com",
        password="testpassword123",
        role="specialist",
    )
    user = crud_user.create_user(db, user_data)
    yield user
    # Cleanup
    db.delete(user)
    db.commit()


@pytest.fixture
def db():
    """Get database session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def test_login_returns_both_tokens(test_user, db):
    """Test that login returns both access and refresh tokens."""
    response = client.post(
        "/users/login",
        json={"email": "test@example.com", "password": "testpassword123"},
    )
    assert response.status_code == 200
    data = response.json()
    assert "access_token" in data
    assert "refresh_token" in data
    assert data["token_type"] == "bearer"
    
    # Verify refresh token is stored in database
    db_token = crud_refresh_token.get_refresh_token(db, data["refresh_token"])
    assert db_token is not None
    assert db_token.user_id == test_user.id
    assert db_token.is_revoked == False


def test_refresh_token_endpoint(test_user, db):
    """Test refreshing access token using refresh token."""
    # First login to get tokens
    login_response = client.post(
        "/users/login",
        json={"email": "test@example.com", "password": "testpassword123"},
    )
    assert login_response.status_code == 200
    refresh_token = login_response.json()["refresh_token"]
    
    # Use refresh token to get new access token
    refresh_response = client.post(
        "/users/refresh",
        json={"refresh_token": refresh_token},
    )
    assert refresh_response.status_code == 200
    data = refresh_response.json()
    assert "access_token" in data
    assert "refresh_token" in data
    assert data["token_type"] == "bearer"
    
    # Old refresh token should be revoked
    old_token = crud_refresh_token.get_refresh_token(db, refresh_token)
    assert old_token is None or old_token.is_revoked == True
    
    # New refresh token should be valid
    new_token = crud_refresh_token.get_refresh_token(db, data["refresh_token"])
    assert new_token is not None
    assert new_token.is_revoked == False


def test_refresh_token_invalid_token():
    """Test refresh with invalid token."""
    # This test doesn't require database setup, so we can run it directly
    response = client.post(
        "/users/refresh",
        json={"refresh_token": "invalid_token"},
    )
    # Should return 401 for invalid token
    assert response.status_code == 401


def test_refresh_token_expired_token(test_user, db):
    """Test refresh with expired token."""
    # Create an expired refresh token
    expired_time = datetime.utcnow() - timedelta(days=1)
    token_str, _ = create_refresh_token({"sub": str(test_user.id), "role": test_user.role})
    
    # Manually set expiration in database
    from app.schemas.refresh_token import RefreshTokenCreate
    token_data = RefreshTokenCreate(
        token=token_str,
        user_id=test_user.id,
        expires_at=expired_time,
    )
    crud_refresh_token.create_refresh_token(db, token_data)
    
    # Try to refresh with expired token
    response = client.post(
        "/users/refresh",
        json={"refresh_token": token_str},
    )
    # Should fail because token is expired
    assert response.status_code == 401


def test_logout_revokes_token(test_user, db):
    """Test that logout revokes refresh token."""
    # Login to get tokens
    login_response = client.post(
        "/users/login",
        json={"email": "test@example.com", "password": "testpassword123"},
    )
    assert login_response.status_code == 200
    access_token = login_response.json()["access_token"]
    refresh_token = login_response.json()["refresh_token"]
    
    # Logout
    logout_response = client.post(
        "/users/logout",
        json={"refresh_token": refresh_token},
        headers={"Authorization": f"Bearer {access_token}"},
    )
    assert logout_response.status_code == 200
    
    # Verify token is revoked
    db_token = crud_refresh_token.get_refresh_token(db, refresh_token)
    assert db_token is None or db_token.is_revoked == True


def test_logout_all_revokes_all_tokens(test_user, db):
    """Test that logout-all revokes all user tokens."""
    # Login multiple times to create multiple tokens
    tokens = []
    for _ in range(3):
        login_response = client.post(
            "/users/login",
            json={"email": "test@example.com", "password": "testpassword123"},
        )
        assert login_response.status_code == 200
        tokens.append(login_response.json())
    
    # Get access token for logout-all
    access_token = tokens[0]["access_token"]
    
    # Logout all
    logout_response = client.post(
        "/users/logout-all",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    assert logout_response.status_code == 200
    assert logout_response.json()["revoked_count"] == 3
    
    # Verify all tokens are revoked
    for token_data in tokens:
        db_token = crud_refresh_token.get_refresh_token(db, token_data["refresh_token"])
        assert db_token is None or db_token.is_revoked == True


def test_refresh_token_after_logout(test_user, db):
    """Test that refresh token cannot be used after logout."""
    # Login
    login_response = client.post(
        "/users/login",
        json={"email": "test@example.com", "password": "testpassword123"},
    )
    assert login_response.status_code == 200
    access_token = login_response.json()["access_token"]
    refresh_token = login_response.json()["refresh_token"]
    
    # Logout
    logout_response = client.post(
        "/users/logout",
        json={"refresh_token": refresh_token},
        headers={"Authorization": f"Bearer {access_token}"},
    )
    assert logout_response.status_code == 200
    
    # Try to refresh with revoked token
    refresh_response = client.post(
        "/users/refresh",
        json={"refresh_token": refresh_token},
    )
    assert refresh_response.status_code == 401


def test_refresh_token_rotation(test_user, db):
    """Test that refresh token is rotated on each refresh."""
    # Login
    login_response = client.post(
        "/users/login",
        json={"email": "test@example.com", "password": "testpassword123"},
    )
    assert login_response.status_code == 200
    refresh_token_1 = login_response.json()["refresh_token"]
    
    # Refresh first time
    refresh_response_1 = client.post(
        "/users/refresh",
        json={"refresh_token": refresh_token_1},
    )
    assert refresh_response_1.status_code == 200
    refresh_token_2 = refresh_response_1.json()["refresh_token"]
    assert refresh_token_1 != refresh_token_2
    
    # Refresh second time
    refresh_response_2 = client.post(
        "/users/refresh",
        json={"refresh_token": refresh_token_2},
    )
    assert refresh_response_2.status_code == 200
    refresh_token_3 = refresh_response_2.json()["refresh_token"]
    assert refresh_token_2 != refresh_token_3
    
    # Old tokens should be revoked
    assert crud_refresh_token.get_refresh_token(db, refresh_token_1) is None
    assert crud_refresh_token.get_refresh_token(db, refresh_token_2) is None
    # New token should be valid
    assert crud_refresh_token.get_refresh_token(db, refresh_token_3) is not None

