def _create_client(client, headers, **overrides):
    payload = {"full_name": "Jana Nováková", "contact_phone": "+420777000111"}
    payload.update(overrides)
    r = client.post("/clients/", json=payload, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


def test_solo_specialist_sees_own_new_client(client, auth_headers):
    created = _create_client(client, auth_headers)

    r = client.get(f"/clients/{created['id']}", headers=auth_headers)
    assert r.status_code == 200, r.text

    r = client.get("/clients/", headers=auth_headers)
    assert r.status_code == 200
    assert [c["id"] for c in r.json()["items"]] == [created["id"]]


def test_other_specialist_does_not_see_the_client(client, auth_headers, make_user, headers_for):
    created = _create_client(client, auth_headers)
    other = headers_for(make_user("specialist"))

    assert client.get(f"/clients/{created['id']}", headers=other).status_code == 403
    assert client.get("/clients/", headers=other).json()["items"] == []


def test_specialist_cannot_list_someone_elses_services(client, make_user, headers_for):
    alice, bob = make_user("specialist"), make_user("specialist")
    r = client.post("/services/", json={"name": "Alice cut", "price": 10, "specialist_id": alice.id}, headers=headers_for(alice))
    assert r.status_code == 200, r.text

    items = client.get("/services/", params={"specialist_id": alice.id}, headers=headers_for(bob)).json()["items"]

    assert items == []


def test_search_and_create_never_reach_another_business(client, make_user, headers_for):
    from tests.tenant_world import MARKER, build_tenant

    a = build_tenant(client, make_user, headers_for, "Alpha", "+42070011")
    b = build_tenant(client, make_user, headers_for, "Bravo", "+42070022")
    a_phone = a["client"]["contact_phone"]

    r = client.get("/clients/search", params={"phone": a_phone}, headers=b["headers"])
    assert r.status_code == 404 and MARKER not in r.text

    r = client.post("/clients/", json={"full_name": "Bravo's new client", "contact_phone": a_phone}, headers=b["headers"])
    assert r.status_code == 200
    assert r.json()["id"] != a["client"]["id"]
    assert MARKER not in r.text

    # own clients are still found
    own = client.get("/clients/search", params={"phone": a_phone}, headers=a["headers"])
    assert own.status_code == 200 and own.json()["id"] == a["client"]["id"]
