import pytest


def _register(client, **overrides):
    body = {
        "full_name": "New User",
        "email": "new@example.com",
        "phone": "+420777123456",
        "password": "Str0ng!Pass-word",
        "role": "specialist",
    }
    body.update(overrides)
    return client.post("/users/", json=body)


@pytest.mark.parametrize("role", ["specialist", "business_owner"])
def test_public_registration_allows_self_service_roles(client, role):
    r = _register(client, role=role)
    assert r.status_code == 200, r.text
    assert r.json()["role"] == role


@pytest.mark.parametrize("role", ["admin", "employee", "client"])
def test_public_registration_rejects_privileged_roles(client, role):
    assert _register(client, role=role).status_code == 403


def test_public_registration_cannot_join_someone_elses_business(client, make_user, headers_for):
    owner_h = headers_for(make_user("business_owner"))
    business = client.post("/businesses/", json={"name": "Salon", "business_type": "salon"}, headers=owner_h).json()

    r = _register(client, business_id=business["id"])

    assert r.status_code == 403
