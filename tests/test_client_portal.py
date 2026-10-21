"""Visitor portal: magic links, separate sessions, tenancy and private data."""
import re
from datetime import timedelta
from urllib.parse import parse_qs, urlsplit

from app.api.core.client_portal_email import KIND as PORTAL_LOGIN_KIND
from app.api.core.clock import utcnow
from app.api.core.notification_templates import render_outbox_notification
from app.api.crud import crud_client_portal
from app.db.models.client_business_relation import ClientBusinessRelation
from app.db.models.client_portal import ClientPortalSession, ClientPortalToken
from app.db.models.loyalty_program import LoyaltyLevel, LoyaltyProgram
from app.db.models.notification_outbox import NotificationOutbox
from app.db.models.questionnaire import QuestionnaireTemplate, QuestionnaireVersion
from app.db.models.questionnaire_response import QuestionnaireResponse

SHARED_EMAIL = "shared.portal@example.com"


def _ok(response, what):
    assert response.status_code in (200, 201, 202), f"{what}: {response.status_code} {response.text}"
    return response.json()


def _salon(client, db, make_user, headers_for, name, email=SHARED_EMAIL, points=0.0):
    owner = make_user(
        "business_owner", full_name=f"{name} Owner", email=f"{name.lower()}.owner@example.com"
    )
    headers = headers_for(owner)
    business = _ok(
        client.post(
            "/businesses/",
            json={"name": f"{name} Salon", "business_type": "salon", "timezone": "Europe/Prague"},
            headers=headers,
        ),
        "business",
    )
    settings = _ok(
        client.put(
            "/businesses/me/public-booking", json={"is_enabled": True}, headers=headers
        ),
        "public settings",
    )
    client_row = _ok(
        client.post(
            "/clients/",
            json={"full_name": f"{name} Guest", "contact_email": email, "language": "en"},
            headers=headers,
        ),
        "client",
    )
    service = _ok(
        client.post(
            "/services/",
            json={
                "name": f"{name} Service",
                "price": 50,
                "duration_minutes": 60,
                "specialist_id": owner.id,
            },
            headers=headers,
        ),
        "service",
    )
    start = (utcnow() + timedelta(days=5)).replace(hour=10, minute=0, second=0, microsecond=0)
    upcoming = _ok(
        client.post(
            "/visits/",
            json={
                "client_id": client_row["id"],
                "specialist_id": owner.id,
                "service_id": service["id"],
                "date_start": start.isoformat(),
                "date_end": (start + timedelta(hours=1)).isoformat(),
                "price": 50,
                "note": "staff only note",
                "payment_method": "cash",
            },
            headers=headers,
        ),
        "upcoming visit",
    )
    past_start = (utcnow() - timedelta(days=10)).replace(hour=9, minute=0, second=0, microsecond=0)
    past = _ok(
        client.post(
            "/visits/",
            json={
                "client_id": client_row["id"],
                "specialist_id": owner.id,
                "service_id": service["id"],
                "date_start": past_start.isoformat(),
                "date_end": (past_start + timedelta(hours=1)).isoformat(),
                "price": 45,
                "note": "past staff note",
                "payment_method": "card",
            },
            headers=headers,
        ),
        "past visit",
    )
    relation = (
        db.query(ClientBusinessRelation)
        .filter(
            ClientBusinessRelation.business_id == business["id"],
            ClientBusinessRelation.client_id == client_row["id"],
        )
        .one()
    )
    program = LoyaltyProgram(business_id=business["id"], program_type="points")
    db.add(program)
    db.flush()
    level = LoyaltyLevel(
        program_id=program.id, name=f"{name} Gold", level_number=1, min_spending=0, benefits="Priority"
    )
    db.add(level)
    db.flush()
    relation.loyalty_points = points
    relation.loyalty_cashback = points / 10
    relation.loyalty_level_id = level.id
    db.commit()
    return {
        "name": name,
        "owner": owner,
        "headers": headers,
        "business": business,
        "settings": settings,
        "client": client_row,
        "service": service,
        "upcoming": upcoming,
        "past": past,
        "points": points,
    }


def _latest_login_row(db, business_id, recipient):
    db.expire_all()
    return (
        db.query(NotificationOutbox)
        .filter(
            NotificationOutbox.kind == PORTAL_LOGIN_KIND,
            NotificationOutbox.business_id == business_id,
            NotificationOutbox.recipient == recipient,
        )
        .order_by(NotificationOutbox.id.desc())
        .first()
    )


def _magic_token(db, business_id, recipient):
    row = _latest_login_row(db, business_id, recipient)
    assert row is not None, "no portal sign-in email was queued"
    message = render_outbox_notification(db, row)
    match = re.search(r"https?://\S+/client/portal#token=\S+", message.text)
    assert match, message.text
    token = parse_qs(urlsplit(match.group(0)).fragment)["token"][0]
    assert token in message.html
    return token, message


def _login(client, db, salon, email=SHARED_EMAIL):
    requested = client.post(
        f"/public/client-portal/{salon['settings']['slug']}/request", json={"email": email}
    )
    assert requested.status_code == 202, requested.text
    token, message = _magic_token(db, salon["business"]["id"], email)
    verified = client.post("/public/client-portal/verify", json={"token": token})
    assert verified.status_code == 200, verified.text
    data = verified.json()
    return data["session_token"], token, message


def _portal_headers(token):
    return {"X-Portal-Token": token}


def test_login_is_enumeration_safe_and_one_time(client, db, make_user, headers_for):
    salon = _salon(client, db, make_user, headers_for, "Enum")
    slug = salon["settings"]["slug"]
    email = salon["client"]["contact_email"]

    unknown_email = client.post(f"/public/client-portal/{slug}/request", json={"email": "nobody@example.com"})
    unknown_slug = client.post("/public/client-portal/not-a-salon/request", json={"email": email})
    known = client.post(f"/public/client-portal/{slug}/request", json={"email": email})
    assert {unknown_email.status_code, unknown_slug.status_code, known.status_code} == {202}
    assert unknown_email.json() == unknown_slug.json() == known.json()
    assert "client of this salon" in known.json()["message"].lower()

    row = _latest_login_row(db, salon["business"]["id"], email)
    assert row is not None
    assert row.recipient == email
    assert row.business_id == salon["business"]["id"]
    assert "token" not in (row.payload or {})  # the outbox keeps no credential

    # a second request inside the cooldown does not queue a duplicate email
    client.post(f"/public/client-portal/{slug}/request", json={"email": email})
    db.expire_all()
    assert (
        db.query(NotificationOutbox)
        .filter(
            NotificationOutbox.kind == PORTAL_LOGIN_KIND,
            NotificationOutbox.business_id == salon["business"]["id"],
        )
        .count()
        == 1
    )

    session_token, magic, message = _login(client, db, salon, email)
    assert "client/portal#token=" in message.text
    assert message.recipient == email

    replay = client.post("/public/client-portal/verify", json={"token": magic})
    assert replay.status_code == 400
    assert "invalid" in replay.json()["error"]["message"].lower()

    me = client.get("/public/client-portal/session", headers=_portal_headers(session_token))
    assert me.status_code == 200
    assert me.json()["client_name"] == salon["client"]["full_name"]
    assert me.json()["business_slug"] == slug

    # the two credential types are not interchangeable
    staff_jwt = salon["headers"]["Authorization"].split(" ", 1)[1]
    assert client.get("/public/client-portal/session", headers=_portal_headers(staff_jwt)).status_code == 401
    assert (
        client.get("/clients/", headers={"Authorization": f"Bearer {session_token}"}).status_code
        == 401
    )

    logout = client.post("/public/client-portal/logout", headers=_portal_headers(session_token))
    assert logout.status_code == 200
    assert client.get("/public/client-portal/session", headers=_portal_headers(session_token)).status_code == 401

    # an independently issued link can expire before it is opened
    expired = crud_client_portal.issue_magic_token(
        db, salon["business"]["id"], salon["client"]["id"]
    )
    db.query(ClientPortalToken).filter(
        ClientPortalToken.business_id == salon["business"]["id"]
    ).update({"expires_at": utcnow() - timedelta(seconds=1)}, synchronize_session=False)
    db.commit()
    assert client.post("/public/client-portal/verify", json={"token": expired}).status_code == 400
    assert client.post("/public/client-portal/verify", json={"token": "x" * 40}).status_code == 400


def test_expired_and_revoked_sessions_stop_working(client, db, make_user, headers_for):
    salon = _salon(client, db, make_user, headers_for, "Expiry")
    session_token, _, _ = _login(client, db, salon)
    session = (
        db.query(ClientPortalSession)
        .filter(ClientPortalSession.business_id == salon["business"]["id"])
        .order_by(ClientPortalSession.id.desc())
        .first()
    )
    session.expires_at = utcnow() - timedelta(seconds=1)
    db.commit()
    assert client.get("/public/client-portal/visits", headers=_portal_headers(session_token)).status_code == 401

    fresh_magic = crud_client_portal.issue_magic_token(
        db, salon["business"]["id"], salon["client"]["id"]
    )
    fresh = client.post("/public/client-portal/verify", json={"token": fresh_magic})
    assert fresh.status_code == 200
    fresh_token = fresh.json()["session_token"]
    # removing the client from the salon ends the session even before its TTL
    db.query(ClientBusinessRelation).filter(
        ClientBusinessRelation.business_id == salon["business"]["id"],
        ClientBusinessRelation.client_id == salon["client"]["id"],
    ).delete(synchronize_session=False)
    db.commit()
    assert client.get("/public/client-portal/session", headers=_portal_headers(fresh_token)).status_code == 401
    assert client.get("/public/client-portal/visits", headers=_portal_headers(fresh_token)).status_code == 401


def test_visits_show_only_own_public_fields_and_open_manage(client, db, make_user, headers_for):
    salon = _salon(client, db, make_user, headers_for, "Visits")
    session_token, _, _ = _login(client, db, salon)
    headers = _portal_headers(session_token)

    data = _ok(client.get("/public/client-portal/visits", headers=headers), "visits")
    assert data["currency"] == "EUR"
    assert data["timezone"] == "Europe/Prague"
    assert data["booking_slug"] == salon["settings"]["slug"]
    assert [visit["id"] for visit in data["upcoming"]] == [salon["upcoming"]["id"]]
    assert [visit["id"] for visit in data["past"]] == [salon["past"]["id"]]
    for visit in data["upcoming"] + data["past"]:
        assert visit["service_name"] == "Visits Service"
        assert visit["staff_name"] == salon["owner"].full_name
        assert "note" not in visit
        assert "payment_method" not in visit
    assert data["upcoming"][0]["can_manage"] is True
    assert data["past"][0]["can_manage"] is False

    link = _ok(
        client.post(
            f"/public/client-portal/visits/{salon['upcoming']['id']}/manage-link", headers=headers
        ),
        "manage link",
    )
    managed = _ok(
        client.get("/public/booking-management", headers={"X-Booking-Token": link["token"]}),
        "managed booking",
    )
    assert managed["booking_id"] == salon["upcoming"]["id"]
    assert managed["service_name"] == "Visits Service"

    # another visit id is not reachable through this session
    assert (
        client.post("/public/client-portal/visits/999999/manage-link", headers=headers).status_code
        == 404
    )


def test_same_email_in_two_salons_has_separate_sessions(client, db, make_user, headers_for):
    alpha = _salon(client, db, make_user, headers_for, "Alpha", points=42.0)
    beta = _salon(client, db, make_user, headers_for, "Beta", points=7.0)

    alpha_token, _, _ = _login(client, db, alpha)
    beta_token, _, _ = _login(client, db, beta)
    alpha_headers = _portal_headers(alpha_token)
    beta_headers = _portal_headers(beta_token)

    alpha_visits = _ok(client.get("/public/client-portal/visits", headers=alpha_headers), "alpha visits")
    beta_visits = _ok(client.get("/public/client-portal/visits", headers=beta_headers), "beta visits")
    assert [visit["service_name"] for visit in alpha_visits["upcoming"]] == ["Alpha Service"]
    assert [visit["service_name"] for visit in beta_visits["upcoming"]] == ["Beta Service"]
    assert alpha_visits["booking_slug"] != beta_visits["booking_slug"]

    # a session cannot open the other salon's visit
    response = client.post(
        f"/public/client-portal/visits/{beta['upcoming']['id']}/manage-link", headers=alpha_headers
    )
    assert response.status_code == 404
    response = client.post(
        f"/public/client-portal/visits/{alpha['upcoming']['id']}/manage-link", headers=beta_headers
    )
    assert response.status_code == 404

    # loyalty is per business and never leaks multipliers or the other salon's balance
    alpha_loyalty = _ok(client.get("/public/client-portal/loyalty", headers=alpha_headers), "alpha loyalty")
    beta_loyalty = _ok(client.get("/public/client-portal/loyalty", headers=beta_headers), "beta loyalty")
    assert alpha_loyalty["points"] == 42.0
    assert beta_loyalty["points"] == 7.0
    assert "points_multiplier" not in alpha_loyalty
    assert "loyalty_level_id" not in alpha_loyalty

    # questionnaires are scoped by business and client as well
    template = QuestionnaireTemplate(
        business_id=alpha["business"]["id"], name="Alpha intake", draft_fields=[]
    )
    db.add(template)
    db.flush()
    version = QuestionnaireVersion(
        template_id=template.id,
        version=1,
        name="Alpha intake",
        fields=[{"key": "mood", "label": "Mood", "type": "text"}],
        published_by_id=alpha["owner"].id,
    )
    db.add(version)
    db.flush()
    db.add(
        QuestionnaireResponse(
            business_id=alpha["business"]["id"],
            visit_id=alpha["past"]["id"],
            version_id=version.id,
            client_id=alpha["client"]["id"],
            answers={"mood": "good"},
            request_hash="alpha-hash",
        )
    )
    db.commit()
    alpha_forms = _ok(
        client.get("/public/client-portal/questionnaires", headers=alpha_headers), "alpha forms"
    )
    beta_forms = _ok(
        client.get("/public/client-portal/questionnaires", headers=beta_headers), "beta forms"
    )
    assert len(alpha_forms["items"]) == 1
    assert alpha_forms["items"][0]["answers"] == {"mood": "good"}
    assert alpha_forms["items"][0]["name"] == "Alpha intake"
    assert beta_forms["items"] == []
