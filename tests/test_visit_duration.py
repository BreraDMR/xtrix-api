"""A visit is as long as its service says, even a short one.

Services may be 1-1440 minutes, but visits had a 15-minute floor - and the floor was also checked
on the way out, so a visit built from a 10-minute service was saved and then answered with a 500.
"""
from datetime import datetime, timedelta

import pytest


@pytest.fixture
def salon(client, make_user, headers_for):
    owner = make_user("business_owner", email="short.owner@example.com")
    h = headers_for(owner)
    assert client.post("/businesses/", json={"name": "Short Salon", "business_type": "salon"}, headers=h).status_code == 200
    cl = client.post("/clients/", json={"full_name": "Quick Client", "contact_phone": "+420700123456"}, headers=h).json()
    svc = client.post("/services/", json={"name": "Brow tint", "price": 10, "duration_minutes": 10,
                                          "specialist_id": owner.id}, headers=h).json()
    start = (datetime.utcnow() + timedelta(days=3)).replace(hour=9, minute=0, second=0, microsecond=0)
    return {"h": h, "owner": owner, "client": cl, "service": svc, "start": start}


def _body(s, **extra):
    return {"client_id": s["client"]["id"], "specialist_id": s["owner"].id,
            "service_id": s["service"]["id"], "date_start": s["start"].isoformat(), **extra}


def test_short_service_without_date_end_is_saved_and_answered(client, salon):
    r = client.post("/visits/", json=_body(salon), headers=salon["h"])
    assert r.status_code == 200, r.text
    visit = r.json()
    assert visit["date_end"] == (salon["start"] + timedelta(minutes=10)).isoformat() + "Z"
    got = client.get(f"/visits/{visit['id']}", headers=salon["h"])
    assert got.status_code == 200, got.text
    listed = client.get("/visits/", headers=salon["h"])
    assert listed.status_code == 200 and visit["id"] in [v["id"] for v in listed.json()["items"]]


def test_the_same_short_visit_can_be_sent_back_as_is(client, salon):
    visit = client.post("/visits/", json=_body(salon), headers=salon["h"]).json()
    # what an edit form does: send the dates it got
    r = client.put(f"/visits/{visit['id']}", json={"date_start": visit["date_start"], "date_end": visit["date_end"],
                                                    "note": "edited"}, headers=salon["h"])
    assert r.status_code == 200, r.text


def test_explicit_short_date_end_is_accepted_too(client, salon):
    end = salon["start"] + timedelta(minutes=5)
    r = client.post("/visits/", json=_body(salon, date_end=end.isoformat()), headers=salon["h"])
    assert r.status_code == 200, r.text


@pytest.mark.parametrize("delta", [timedelta(0), timedelta(minutes=-5), timedelta(hours=25)])
def test_impossible_lengths_are_still_refused(client, salon, delta):
    end = salon["start"] + delta
    r = client.post("/visits/", json=_body(salon, date_end=end.isoformat()), headers=salon["h"])
    assert r.status_code == 422, r.text
    assert r.json()["error"]["detail"]["validation_errors"][0]["code"] == "invalid_time_range"
