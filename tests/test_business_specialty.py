"""Specialty picked at signup: Google pilot signup and the business profile both take it,
/users/me and /businesses/me give it back together with its category, so the front can adapt."""
import pytest

from app.api.core.config import settings
from tests.test_google_login import ORIGIN, sign_in


@pytest.fixture
def google_on(monkeypatch):
    monkeypatch.setattr(settings, "google_client_id", "pilot.apps.googleusercontent.com")
    monkeypatch.setattr(settings, "frontend_url", ORIGIN)
    monkeypatch.setattr(settings, "pilot_mode_enabled", True)
    monkeypatch.setattr(settings, "pilot_invited_emails", "invited@example.com")


def _business(client, headers, **extra):
    body = {"name": "Spec Salon", "business_type": "salon", **extra}
    return client.post("/businesses/", json=body, headers=headers)


def test_google_signup_keeps_the_picked_specialty(client, db, monkeypatch, google_on):
    response = sign_in(client, monkeypatch, specialist_type="dentist")
    assert response.status_code == 200, response.text
    headers = {"Authorization": f"Bearer {response.json()['access_token']}"}
    me = client.get("/users/me", headers=headers).json()
    assert me["specialist_type"] == "dentist"
    assert me["specialist_category"] == "healthcare"
    # the owner's pick becomes the business default when the form leaves it out
    created = _business(client, headers)
    assert created.status_code == 200, created.text
    assert created.json()["specialist_type"] == "dentist"
    assert created.json()["specialist_category"] == "healthcare"


def test_google_signup_rejects_unknown_specialty_and_creates_nobody(client, db, monkeypatch, google_on):
    from app.db.models.user import User

    response = sign_in(client, monkeypatch, specialist_type="astrologer")
    assert response.status_code == 422
    assert db.query(User).filter_by(email="invited@example.com").count() == 0


def test_google_signup_without_specialty_still_works(client, monkeypatch, google_on):
    response = sign_in(client, monkeypatch)
    assert response.status_code == 200, response.text
    me = client.get("/users/me", headers={"Authorization": f"Bearer {response.json()['access_token']}"}).json()
    assert me["specialist_type"] is None
    assert me["specialist_category"] is None


def test_second_google_sign_in_does_not_overwrite_the_specialty(client, db, monkeypatch, google_on):
    from app.db.models.user import User

    assert sign_in(client, monkeypatch, specialist_type="barber").status_code == 200
    assert sign_in(client, monkeypatch, specialist_type="dentist").status_code == 200
    db.expire_all()
    assert db.query(User).filter_by(email="invited@example.com").one().specialist_type == "barber"


def test_business_profile_takes_changes_and_clears_the_specialty(client, make_user, headers_for):
    owner = make_user("business_owner")
    headers = headers_for(owner)
    created = _business(client, headers, specialist_type="massage")
    assert created.status_code == 200, created.text
    business_id = created.json()["id"]
    assert created.json()["specialist_category"] == "beauty"

    mine = client.get("/businesses/me", headers=headers).json()
    assert (mine["specialist_type"], mine["specialist_category"]) == ("massage", "beauty")

    updated = client.put(f"/businesses/{business_id}", json={"specialist_type": "yoga_instructor"}, headers=headers)
    assert updated.status_code == 200, updated.text
    assert (updated.json()["specialist_type"], updated.json()["specialist_category"]) == ("yoga_instructor", "wellness")

    bad = client.put(f"/businesses/{business_id}", json={"specialist_type": "astrologer"}, headers=headers)
    assert bad.status_code == 422

    cleared = client.put(f"/businesses/{business_id}", json={"specialist_type": None}, headers=headers)
    assert cleared.status_code == 200, cleared.text
    assert (cleared.json()["specialist_type"], cleared.json()["specialist_category"]) == (None, None)
    mine = client.get("/businesses/me", headers=headers).json()
    assert mine["specialist_type"] is None


def test_business_without_any_specialty_reports_none(client, make_user, headers_for):
    owner = make_user("business_owner")
    created = _business(client, headers_for(owner))
    assert created.status_code == 200, created.text
    assert created.json()["specialist_type"] is None
    assert created.json()["specialist_category"] is None


def test_staff_cannot_change_the_business_specialty(client, db, make_user, headers_for):
    owner = make_user("business_owner")
    business_id = _business(client, headers_for(owner), specialist_type="dentist").json()["id"]
    stranger = make_user("business_owner")
    denied = client.put(f"/businesses/{business_id}", json={"specialist_type": "barber"}, headers=headers_for(stranger))
    assert denied.status_code == 403
    mine = client.get("/businesses/me", headers=headers_for(owner)).json()
    assert mine["specialist_type"] == "dentist"


def test_specialty_migration_round_trip_keeps_businesses(db, make_user, client, headers_for):
    from alembic import command
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    from sqlalchemy import inspect, text

    owner = make_user("business_owner")
    business_id = _business(client, headers_for(owner), specialist_type="dentist").json()["id"]
    config = Config("alembic.ini")
    assert ScriptDirectory.from_config(config).get_heads() == ["0095_contacts_client_contacts"]
    db.rollback()
    bind = db.get_bind()
    try:
        command.downgrade(config, "0092_visit_settlement_entries")
        assert "specialist_type" not in {c["name"] for c in inspect(bind).get_columns("businesses")}
        with bind.connect() as conn:
            assert conn.execute(text("SELECT name FROM businesses WHERE id = :id"), {"id": business_id}).scalar_one() == "Spec Salon"
        command.upgrade(config, "head")
        with bind.connect() as conn:
            # the pick is gone after a downgrade, the business itself stays
            row = conn.execute(text("SELECT name, specialist_type FROM businesses WHERE id = :id"), {"id": business_id}).one()
        assert tuple(row) == ("Spec Salon", None)
    finally:
        db.rollback()
        command.upgrade(config, "head")
    assert db.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == "0095_contacts_client_contacts"


@pytest.mark.parametrize("specialty", ["pmu_artist", "depilation_specialist"])
def test_salon_specialties_added_in_november_are_beauty(client, monkeypatch, google_on, specialty):
    # permanent make-up and depilation are in almost every Prague salon, they must be pickable
    types = client.get("/specialist-profiles/types").json()["categories"]["beauty"]["types"]
    assert specialty in types
    response = sign_in(client, monkeypatch, specialist_type=specialty)
    assert response.status_code == 200, response.text
    me = client.get("/users/me", headers={"Authorization": f"Bearer {response.json()['access_token']}"}).json()
    assert me["specialist_category"] == "beauty"
