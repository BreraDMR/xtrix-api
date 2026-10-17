"""A solo specialist has no business, so their visits carry business_id NULL. Business owners must not
see them, and must not be able to pull such a specialist into their business by id."""
from datetime import datetime, timedelta

import pytest


@pytest.fixture
def world(client, make_user, headers_for):
    solo = make_user("specialist", specialist_type="massage")
    sh = headers_for(solo)
    solo_client = client.post("/clients/", json={"full_name": "Private client of Solo"}, headers=sh).json()
    start = (datetime.utcnow() + timedelta(days=3)).replace(hour=9, minute=0, second=0, microsecond=0)
    r = client.post("/visits/", json={
        "client_id": solo_client["id"], "specialist_id": solo.id,
        "date_start": start.isoformat(), "date_end": (start + timedelta(hours=1)).isoformat(),
    }, headers=sh)
    assert r.status_code == 200, r.text

    owner = make_user("business_owner")
    oh = headers_for(owner)
    assert client.post("/businesses/", json={"name": "Other Salon", "business_type": "salon"}, headers=oh).status_code == 200
    return {"solo": solo, "sh": sh, "visit": r.json(), "owner": owner, "oh": oh}


def test_owner_does_not_see_solo_visits(client, world):
    r = client.get("/visits/", headers=world["oh"])
    assert r.status_code == 200, r.text
    items = r.json()["items"] if isinstance(r.json(), dict) else r.json()
    assert world["visit"]["id"] not in [v["id"] for v in items]


def test_solo_still_sees_own_visits(client, world):
    r = client.get("/visits/", headers=world["sh"])
    items = r.json()["items"] if isinstance(r.json(), dict) else r.json()
    assert world["visit"]["id"] in [v["id"] for v in items]


def test_owner_cannot_hire_an_outside_specialist_by_id(client, world):
    r = client.post("/employees/", json={"employee_user_id": world["solo"].id}, headers=world["oh"])
    assert r.status_code == 403, r.text
    # and the specialist stays on their own
    me = client.get("/users/me", headers=world["sh"]).json()
    assert me.get("business_id") in (None, 0)
    items = client.get("/visits/", headers=world["oh"]).json()
    items = items["items"] if isinstance(items, dict) else items
    assert world["visit"]["id"] not in [v["id"] for v in items]


def test_hire_by_id_only_inside_own_business(client, make_user, world, db):
    owner, oh = world["owner"], world["oh"]
    db.refresh(owner)
    # someone already in the owner's business but not hired yet - the by-id path is for them
    insider = make_user("specialist", business_id=owner.business_id)
    r = client.post("/employees/", json={"employee_user_id": insider.id}, headers=oh)
    assert r.status_code == 200, r.text
    assert client.delete(f"/employees/{r.json()['id']}", headers=oh).status_code == 200
    # firing took them out of the business, so they are an outsider now
    r = client.post("/employees/", json={"employee_user_id": insider.id}, headers=oh)
    assert r.status_code == 403, r.text
