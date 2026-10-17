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


def test_user_list_is_limited_to_own_business(client, make_user, headers_for):
    def owner_with_business(name):
        owner = make_user("business_owner", full_name=f"{name} Owner")
        h = headers_for(owner)
        client.post("/businesses/", json={"name": name, "business_type": "salon"}, headers=h)
        return h

    a, b = owner_with_business("Alpha"), owner_with_business("Bravo")
    make_user("specialist", full_name="Lonely Specialist")

    names = [u["full_name"] for u in client.get("/users/", headers=b).json()["items"]]
    assert names == ["Bravo Owner"]
    admin = headers_for(make_user("admin"))
    assert client.get("/users/", headers=admin).json()["total"] == 4
