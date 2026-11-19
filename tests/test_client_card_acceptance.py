"""PROFILE-09 acceptance: the client card checked against real permissions.

The package tests already cover CRUD, schemas and the basic staff rules. These go
after the gaps a reviewer cares about: one client row known to two salons, staff
who were fired or limited to some clients, visits that were deleted or archived,
and money that must stay hidden in every part of the overview, not just history.
"""
from datetime import datetime, timedelta

from app.db.models.client_business_relation import ClientBusinessRelation
from app.db.models.user import User
from app.db.models.visit import Visit


def _salon(client, make_user, headers_for, name, currency="EUR"):
    owner = make_user("business_owner", full_name=f"{name} Owner", email=f"{name.lower()}.card@example.com")
    headers = headers_for(owner)
    business = client.post(
        "/businesses/",
        json={"name": f"{name} Salon", "business_type": "salon", "currency": currency},
        headers=headers,
    )
    assert business.status_code == 200, business.text
    return {"owner": owner, "headers": headers, "business": business.json()}


def _new_client(client, salon, name):
    row = client.post("/clients/", json={"full_name": name}, headers=salon["headers"])
    assert row.status_code == 200, row.text
    return row.json()


def _visit(client, salon, client_id, start, **fields):
    start = start.replace(microsecond=0)
    body = {
        "client_id": client_id,
        "specialist_id": salon["owner"].id,
        "date_start": start.isoformat(),
        "date_end": (start + timedelta(hours=1)).isoformat(),
        "service_name": fields.pop("service_name", "Haircut"),
        **fields,
    }
    response = client.post("/visits/", json=body, headers=salon["headers"])
    assert response.status_code == 200, response.text
    return response.json()


def _staff(client, salon, db, headers_for, email, permissions=None):
    created = client.post("/employees/create-with-user", json={
        "full_name": f"Staff {email}", "email": email,
    }, headers=salon["headers"])
    assert created.status_code == 200, created.text
    row = created.json()
    if permissions is not None:
        updated = client.put(f"/employees/{row['id']}", json={"permissions": permissions}, headers=salon["headers"])
        assert updated.status_code == 200, updated.text
    return row, headers_for(db.get(User, row["employee_user_id"]))


def _overview(client, headers, client_id):
    response = client.get(f"/clients/{client_id}/overview", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def test_one_client_row_in_two_salons_keeps_each_card_separate(client, make_user, headers_for, db):
    alpha = _salon(client, make_user, headers_for, "Cardalpha", currency="CZK")
    beta = _salon(client, make_user, headers_for, "Cardbeta", currency="EUR")
    shared = _new_client(client, alpha, "Shared Person")
    now = datetime.utcnow()

    alpha_visit = _visit(client, alpha, shared["id"], now - timedelta(days=3), status="completed",
                         price=500, payment_method="card", service_name="Alpha colour")
    _visit(client, alpha, shared["id"], now + timedelta(days=2), price=800, service_name="Alpha cut")
    assert client.post(f"/clients/{shared['id']}/notes", json={"body": "Alpha only"},
                       headers=alpha["headers"]).status_code == 201
    assert client.put(f"/clients/{shared['id']}/profile", json={"tags": ["alpha-vip"], "preferences": "Alpha pref"},
                      headers=alpha["headers"]).status_code == 200

    # the same client row gets linked to a second salon (booking, import, visitor claim all do this)
    db.add(ClientBusinessRelation(client_id=shared["id"], business_id=beta["business"]["id"]))
    db.commit()
    _visit(client, beta, shared["id"], now - timedelta(days=1), status="completed",
           price=70, payment_method="cash", service_name="Beta nails")

    beta_card = _overview(client, beta["headers"], shared["id"])
    assert beta_card["stats"]["total_visits"] == 1
    assert [v["service_name"] for v in beta_card["recent_visits"]] == ["Beta nails"]
    assert beta_card["upcoming_visits"] == [] and beta_card["next_visit"] is None
    assert beta_card["payments"] == {"currency": "EUR", "total_paid": 70.0, "paid_visits": 1}

    alpha_card = _overview(client, alpha["headers"], shared["id"])
    assert alpha_card["stats"]["total_visits"] == 2
    assert [v["service_name"] for v in alpha_card["recent_visits"]] == ["Alpha colour"]
    assert alpha_card["payments"] == {"currency": "CZK", "total_paid": 500.0, "paid_visits": 1}

    assert client.get(f"/clients/{shared['id']}/notes", headers=beta["headers"]).json() == []
    beta_profile = client.get(f"/clients/{shared['id']}/profile", headers=beta["headers"]).json()
    assert beta_profile["tags"] == [] and beta_profile["preferences"] is None

    # beta writing its own tags must not overwrite alpha's
    assert client.put(f"/clients/{shared['id']}/profile", json={"tags": ["beta"]},
                      headers=beta["headers"]).status_code == 200
    alpha_profile = client.get(f"/clients/{shared['id']}/profile", headers=alpha["headers"]).json()
    assert alpha_profile == {"client_id": shared["id"], "tags": ["alpha-vip"], "preferences": "Alpha pref"}

    # alpha's visit questionnaire stays closed to beta even though beta knows the client
    assert client.get(f"/visits/{alpha_visit['id']}/questionnaire", headers=beta["headers"]).status_code == 403
    assert client.get(f"/visits/{alpha_visit['id']}/questionnaire/attachments",
                      headers=beta["headers"]).status_code == 403


def test_staff_without_financials_sees_no_money_anywhere_in_the_card(client, make_user, headers_for, db):
    salon = _salon(client, make_user, headers_for, "Cardmoney")
    person = _new_client(client, salon, "Money Person")
    now = datetime.utcnow()
    _visit(client, salon, person["id"], now - timedelta(days=5), status="completed", price=120, payment_method="card")
    _visit(client, salon, person["id"], now + timedelta(days=1), price=300, payment_method="cash")
    _visit(client, salon, person["id"], now + timedelta(days=4), price=310, payment_method="card")
    _, staff_headers = _staff(client, salon, db, headers_for, "card.money@example.com")

    card = _overview(client, staff_headers, person["id"])
    assert card["stats"]["total_visits"] == 3
    assert card["payments"] is None
    shown = [card["next_visit"], *card["upcoming_visits"], *card["recent_visits"]]
    assert len(shown) == 4
    assert all(v["price"] is None and v["payment_method"] is None for v in shown)

    owner_card = _overview(client, salon["headers"], person["id"])
    assert owner_card["next_visit"]["price"] == 300
    assert [v["price"] for v in owner_card["upcoming_visits"]] == [300, 310]


def test_deleted_and_archived_visits_leave_the_card(client, make_user, headers_for, db):
    salon = _salon(client, make_user, headers_for, "Cardgone")
    person = _new_client(client, salon, "Gone Person")
    now = datetime.utcnow()
    kept = _visit(client, salon, person["id"], now - timedelta(days=9), status="completed", price=40)
    deleted = _visit(client, salon, person["id"], now - timedelta(days=6), status="completed", price=50)
    archived = _visit(client, salon, person["id"], now - timedelta(days=3), status="completed", price=60)
    upcoming_deleted = _visit(client, salon, person["id"], now + timedelta(days=3), price=70)

    for visit_id in (deleted["id"], upcoming_deleted["id"]):
        assert client.delete(f"/visits/{visit_id}", headers=salon["headers"]).status_code == 200
    db.get(Visit, archived["id"]).archived_at = now
    db.commit()

    card = _overview(client, salon["headers"], person["id"])
    assert card["stats"]["total_visits"] == 1
    assert card["stats"]["completed_visits"] == 1
    assert [v["id"] for v in card["recent_visits"]] == [kept["id"]]
    assert card["next_visit"] is None and card["upcoming_visits"] == []
    assert card["payments"]["total_paid"] == 40.0


def test_fired_staff_loses_the_whole_card(client, make_user, headers_for, db):
    salon = _salon(client, make_user, headers_for, "Cardfired")
    person = _new_client(client, salon, "Fired Person")
    row, staff_headers = _staff(client, salon, db, headers_for, "card.fired@example.com")
    path = f"/clients/{person['id']}"
    assert client.get(f"{path}/overview", headers=staff_headers).status_code == 200
    assert client.get(f"{path}/notes", headers=staff_headers).status_code == 200

    fired = client.delete(f"/employees/{row['id']}", headers=salon["headers"])
    assert fired.status_code == 200, fired.text

    for suffix in ("overview", "notes", "profile"):
        response = client.get(f"{path}/{suffix}", headers=staff_headers)
        assert response.status_code in (401, 403), (suffix, response.status_code, response.text)
    assert client.post(f"{path}/notes", json={"body": "after firing"},
                       headers=staff_headers).status_code in (401, 403)


def test_assigned_staff_gets_questionnaires_only_for_reachable_visits(client, make_user, headers_for, db):
    salon = _salon(client, make_user, headers_for, "Cardform")
    visible = _new_client(client, salon, "Form Visible")
    hidden = _new_client(client, salon, "Form Hidden")
    now = datetime.utcnow()
    visible_visit = _visit(client, salon, visible["id"], now - timedelta(days=1), status="completed")
    hidden_visit = _visit(client, salon, hidden["id"], now - timedelta(days=2), status="completed")
    _, staff_headers = _staff(client, salon, db, headers_for, "card.form@example.com",
                              permissions={"assigned_client_ids": [visible["id"]]})

    # reachable visit without answers: "not filled", not "forbidden"
    assert client.get(f"/visits/{visible_visit['id']}/questionnaire", headers=staff_headers).status_code == 404
    assert client.get(f"/visits/{visible_visit['id']}/questionnaire/attachments",
                      headers=staff_headers).json() == []
    assert client.get(f"/visits/{hidden_visit['id']}/questionnaire", headers=staff_headers).status_code == 403
    assert client.get(f"/visits/{hidden_visit['id']}/questionnaire/attachments",
                      headers=staff_headers).status_code == 403
    for suffix in ("overview", "notes", "profile"):
        assert client.get(f"/clients/{hidden['id']}/{suffix}", headers=staff_headers).status_code == 403


def test_partial_profile_update_keeps_the_other_field(client, make_user, headers_for):
    salon = _salon(client, make_user, headers_for, "Cardpartial")
    person = _new_client(client, salon, "Partial Person")
    path = f"/clients/{person['id']}/profile"
    assert client.put(path, json={"tags": ["a"], "preferences": "Quiet room"}, headers=salon["headers"]).status_code == 200

    assert client.put(path, json={"tags": ["a", "b"]}, headers=salon["headers"]).json()["preferences"] == "Quiet room"
    assert client.put(path, json={"preferences": "Window seat"}, headers=salon["headers"]).json()["tags"] == ["a", "b"]
    cleared = client.put(path, json={"preferences": "   "}, headers=salon["headers"]).json()
    assert cleared == {"client_id": person["id"], "tags": ["a", "b"], "preferences": None}
