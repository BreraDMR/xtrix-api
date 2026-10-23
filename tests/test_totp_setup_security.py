"""An existing second factor cannot be replaced by a bearer session alone."""
import pyotp
import pytest


@pytest.mark.parametrize("role", ["specialist", "business_owner"])
def test_setup_preserves_enabled_second_factor(client, db, make_user, headers_for, role):
    user = make_user(role)
    secret = pyotp.random_base32()
    user.totp_secret = secret
    user.is_2fa_enabled = True
    db.commit()

    response = client.post("/users/2fa/setup", headers=headers_for(user))

    assert response.status_code == 409, response.text
    assert "secret" not in response.json()
    db.refresh(user)
    assert user.is_2fa_enabled is True
    assert user.totp_secret == secret
    login = client.post("/users/login", json={
        "email": user.email, "password": "Str0ng!Pass-word",
    })
    assert login.status_code == 200
    assert login.json()["requires_2fa"] is True
    assert not login.json().get("access_token")


def test_setup_after_verified_disable_can_enroll_again(client, db, make_user, headers_for):
    user = make_user()
    secret = pyotp.random_base32()
    user.totp_secret = secret
    user.is_2fa_enabled = True
    db.commit()
    headers = headers_for(user)
    disabled = client.post("/users/2fa/disable", headers=headers,
                           json={"token": pyotp.TOTP(secret).now()})
    assert disabled.status_code == 200, disabled.text
    setup = client.post("/users/2fa/setup", headers=headers)
    assert setup.status_code == 200, setup.text
    new_secret = setup.json()["secret"]
    assert new_secret != secret
    enabled = client.post("/users/2fa/enable", headers=headers,
                          json={"token": pyotp.TOTP(new_secret).now()})
    assert enabled.status_code == 200, enabled.text
    db.refresh(user)
    assert user.is_2fa_enabled is True
    assert user.totp_secret == new_secret
