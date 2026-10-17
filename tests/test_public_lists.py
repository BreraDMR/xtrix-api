"""Lists that used to leak the whole platform, and paging values that used to 500."""
import pytest


def _owner_with_business(client, make_user, headers_for, name):
    owner = make_user(role="business_owner")
    h = headers_for(owner)
    r = client.post("/businesses/", json={"name": name, "business_type": "salon"}, headers=h)
    assert r.status_code == 200, r.text
    return owner, h, r.json()["id"]


def test_anonymous_cannot_list_businesses(client, make_user, headers_for):
    _owner_with_business(client, make_user, headers_for, "Salon A")
    r = client.get("/businesses/")
    assert r.status_code == 401


def test_owner_lists_only_own_business(client, make_user, headers_for):
    _, ha, a_id = _owner_with_business(client, make_user, headers_for, "Salon A")
    _owner_with_business(client, make_user, headers_for, "Salon B")
    r = client.get("/businesses/", headers=ha)
    assert r.status_code == 200
    assert [b["id"] for b in r.json()] == [a_id]


def test_admin_list_is_capped(client, make_user, headers_for):
    admin = make_user(role="admin")
    r = client.get("/businesses/?limit=100000", headers=headers_for(admin))
    assert r.status_code == 422


@pytest.mark.parametrize("query", ["skip=-1", "limit=0", "limit=-5", "skip=99999999999999999999"])
def test_bad_business_paging_is_422_not_500(client, make_user, headers_for, query):
    admin = make_user(role="admin")
    r = client.get(f"/businesses/?{query}", headers=headers_for(admin))
    assert r.status_code == 422


@pytest.mark.parametrize("path", ["/clients/", "/visits/", "/services/", "/products/", "/expenses/", "/employees/"])
def test_huge_page_number_is_422_not_500(client, make_user, headers_for, path):
    _, h, _ = _owner_with_business(client, make_user, headers_for, "Salon P")
    r = client.get(f"{path}?page=99999999999999999999", headers=h)
    assert r.status_code == 422, r.text


def test_specialist_profiles_list_stays_in_own_business(client, make_user, headers_for, db):
    from app.db.models.user import User

    _, ha, a_id = _owner_with_business(client, make_user, headers_for, "Salon A")
    _, _, b_id = _owner_with_business(client, make_user, headers_for, "Salon B")
    spec_a = make_user(role="specialist")
    spec_b = make_user(role="specialist")
    solo = make_user(role="specialist")
    db.query(User).filter(User.id == spec_a.id).update({"business_id": a_id})
    db.query(User).filter(User.id == spec_b.id).update({"business_id": b_id})
    db.commit()
    for spec in (spec_a, spec_b, solo):
        r = client.post("/specialist-profiles/", json={"user_id": spec.id, "specialist_type": "massage"}, headers=headers_for(spec))
        assert r.status_code == 200, r.text

    seen_by_a = {p["user_id"] for p in client.get("/specialist-profiles/", headers=headers_for(spec_a)).json()}
    assert seen_by_a == {spec_a.id}
    seen_by_solo = {p["user_id"] for p in client.get("/specialist-profiles/", headers=headers_for(solo)).json()}
    assert seen_by_solo == {solo.id}

    admin = make_user(role="admin")
    seen_by_admin = {p["user_id"] for p in client.get("/specialist-profiles/", headers=headers_for(admin)).json()}
    assert {spec_a.id, spec_b.id, solo.id} <= seen_by_admin
