"""Business-scoped client notes: CRUD, tenant isolation and staff permissions.

Two salons may know the same phone number; that must never merge their internal notes.
"""
from app.db.models.client_note import ClientNote
from app.db.models.user import User


def _owner_salon(client, make_user, headers_for, name, phone):
    owner = make_user("business_owner", full_name=f"{name} Owner", email=f"{name.lower()}.owner@example.com")
    headers = headers_for(owner)
    business = client.post(
        "/businesses/", json={"name": f"{name} Salon", "business_type": "salon"}, headers=headers
    )
    assert business.status_code == 200, business.text
    row = client.post(
        "/clients/",
        json={"full_name": f"{name} Client", "contact_phone": phone, "contact_email": f"{name.lower()}@example.com"},
        headers=headers,
    )
    assert row.status_code == 200, row.text
    return {"owner": owner, "headers": headers, "client": row.json(), "business": business.json()}


def _errors(response):
    assert response.status_code == 422, response.text
    return {item["field"].split(" -> ")[-1]: item for item in response.json()["error"]["detail"]["validation_errors"]}


def test_owner_edits_notes_tags_and_preferences(client, make_user, headers_for):
    salon = _owner_salon(client, make_user, headers_for, "Note", "+420700009001")
    h, client_id = salon["headers"], salon["client"]["id"]

    created = client.post(f"/clients/{client_id}/notes", json={"body": "  Prefers a quiet room  "}, headers=h)
    assert created.status_code == 201, created.text
    note = created.json()
    assert note["body"] == "Prefers a quiet room"
    assert note["client_id"] == client_id
    assert note["business_id"] == salon["business"]["id"]
    assert note["author_id"] == salon["owner"].id
    assert note["author_name"] == salon["owner"].full_name

    listed = client.get(f"/clients/{client_id}/notes", headers=h)
    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()] == [note["id"]]

    updated = client.put(
        f"/clients/{client_id}/notes/{note['id']}", json={"body": "Prefers mornings"}, headers=h
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["body"] == "Prefers mornings"
    assert updated.json()["created_at"] == note["created_at"]
    assert updated.json()["updated_at"] >= note["updated_at"]

    profile = client.put(
        f"/clients/{client_id}/profile",
        json={"tags": ["vip", "morning"], "preferences": "Call before visiting"},
        headers=h,
    )
    assert profile.status_code == 200, profile.text
    assert profile.json() == {
        "client_id": client_id,
        "tags": ["vip", "morning"],
        "preferences": "Call before visiting",
    }
    assert client.get(f"/clients/{client_id}/profile", headers=h).json() == profile.json()

    # a partial update leaves the tags alone
    only_preferences = client.put(
        f"/clients/{client_id}/profile", json={"preferences": "No calls"}, headers=h
    )
    assert only_preferences.json()["tags"] == ["vip", "morning"]
    assert only_preferences.json()["preferences"] == "No calls"

    deleted = client.delete(f"/clients/{client_id}/notes/{note['id']}", headers=h)
    assert deleted.status_code == 200, deleted.text
    assert client.get(f"/clients/{client_id}/notes", headers=h).json() == []
    assert client.put(
        f"/clients/{client_id}/notes/{note['id']}", json={"body": "gone"}, headers=h
    ).status_code == 404


def test_note_text_is_validated_through_the_api(client, make_user, headers_for):
    salon = _owner_salon(client, make_user, headers_for, "Text", "+420700009002")
    h, path = salon["headers"], f"/clients/{salon['client']['id']}/notes"

    assert _errors(client.post(path, json={"body": "<b>VIP</b>"}, headers=h))["body"]["code"] == "html_not_allowed"
    assert _errors(client.post(path, json={"body": "x" * 2001}, headers=h))["body"]["code"] == "string_too_long"
    assert _errors(client.post(path, json={"body": "ok", "allergies": "latex"}, headers=h))["allergies"]["code"] == "extra_forbidden"
    assert _errors(
        client.put(f"/clients/{salon['client']['id']}/profile", json={"tags": ["a", "A"]}, headers=h)
    )["tags"]["code"] == "duplicate_tag"


def test_same_phone_in_another_business_does_not_share_notes(client, make_user, headers_for, db):
    phone = "+420700009100"
    alpha = _owner_salon(client, make_user, headers_for, "Alpha", phone)
    beta = _owner_salon(client, make_user, headers_for, "Beta", phone)
    assert alpha["client"]["id"] != beta["client"]["id"]

    alpha_note = client.post(
        f"/clients/{alpha['client']['id']}/notes", json={"body": "Alpha private note"}, headers=alpha["headers"]
    ).json()
    client.put(
        f"/clients/{alpha['client']['id']}/profile",
        json={"tags": ["alpha-only"], "preferences": "Alpha preference"},
        headers=alpha["headers"],
    )
    beta_note = client.post(
        f"/clients/{beta['client']['id']}/notes", json={"body": "Beta private note"}, headers=beta["headers"]
    ).json()

    assert [n["body"] for n in client.get(
        f"/clients/{alpha['client']['id']}/notes", headers=alpha["headers"]
    ).json()] == ["Alpha private note"]
    assert [n["body"] for n in client.get(
        f"/clients/{beta['client']['id']}/notes", headers=beta["headers"]
    ).json()] == ["Beta private note"]
    assert client.get(f"/clients/{alpha['client']['id']}/notes", headers=beta["headers"]).status_code == 403

    # the client id is reachable for Beta, but Alpha's note id is not
    assert client.put(
        f"/clients/{beta['client']['id']}/notes/{alpha_note['id']}",
        json={"body": "stolen"}, headers=beta["headers"],
    ).status_code == 404
    assert client.delete(
        f"/clients/{beta['client']['id']}/notes/{alpha_note['id']}", headers=beta["headers"]
    ).status_code == 404

    beta_profile = client.get(f"/clients/{beta['client']['id']}/profile", headers=beta["headers"]).json()
    assert beta_profile["tags"] == [] and beta_profile["preferences"] is None

    rows = db.query(ClientNote).filter(ClientNote.id.in_([alpha_note["id"], beta_note["id"]])).all()
    assert {row.business_id for row in rows} == {alpha["business"]["id"], beta["business"]["id"]}


def test_note_id_belongs_to_the_client_in_the_path(client, make_user, headers_for):
    salon = _owner_salon(client, make_user, headers_for, "Path", "+420700009200")
    h = salon["headers"]
    other = client.post("/clients/", json={"full_name": "Path Other"}, headers=h)
    assert other.status_code == 200, other.text
    note = client.post(
        f"/clients/{salon['client']['id']}/notes", json={"body": "Only for the first client"}, headers=h
    ).json()

    assert client.put(
        f"/clients/{other.json()['id']}/notes/{note['id']}", json={"body": "wrong client"}, headers=h
    ).status_code == 404
    assert client.delete(f"/clients/{other.json()['id']}/notes/{note['id']}", headers=h).status_code == 404


def test_staff_reads_notes_but_needs_the_edit_right(client, make_user, headers_for, db):
    salon = _owner_salon(client, make_user, headers_for, "Staffnote", "+420700009300")
    employee = client.post("/employees/create-with-user", json={
        "full_name": "Note Staff", "email": "note.staff@example.com",
    }, headers=salon["headers"])
    assert employee.status_code == 200, employee.text
    employee_row = employee.json()
    staff_user = db.get(User, employee_row["employee_user_id"])
    staff_headers = headers_for(staff_user)
    path = f"/clients/{salon['client']['id']}"

    # defaults allow editing clients, so take the right away explicitly
    client.put(
        f"/employees/{employee_row['id']}",
        json={"permissions": {"can_edit_clients": False}},
        headers=salon["headers"],
    )
    assert client.get(f"{path}/notes", headers=staff_headers).status_code == 200
    assert client.get(f"{path}/profile", headers=staff_headers).status_code == 200
    assert client.post(f"{path}/notes", json={"body": "nope"}, headers=staff_headers).status_code == 403
    assert client.put(f"{path}/profile", json={"tags": ["nope"]}, headers=staff_headers).status_code == 403

    client.put(
        f"/employees/{employee_row['id']}",
        json={"permissions": {"can_edit_clients": True}},
        headers=salon["headers"],
    )
    assert client.post(f"{path}/notes", json={"body": "allowed"}, headers=staff_headers).status_code == 201
    assert client.put(f"{path}/profile", json={"tags": ["allowed"]}, headers=staff_headers).status_code == 200


def test_assigned_clients_limit_notes_too(client, make_user, headers_for, db):
    salon = _owner_salon(client, make_user, headers_for, "Assigned", "+420700009400")
    visible = client.post("/clients/", json={"full_name": "Visible Client"}, headers=salon["headers"]).json()
    hidden = client.post("/clients/", json={"full_name": "Hidden Client"}, headers=salon["headers"]).json()
    employee = client.post("/employees/create-with-user", json={
        "full_name": "Assigned Staff", "email": "assigned.staff@example.com",
    }, headers=salon["headers"])
    employee_row = employee.json()
    client.put(
        f"/employees/{employee_row['id']}",
        json={"permissions": {"can_edit_clients": True, "assigned_client_ids": [visible["id"]]}},
        headers=salon["headers"],
    )
    staff_headers = headers_for(db.get(User, employee_row["employee_user_id"]))

    assert client.get(f"/clients/{visible['id']}/notes", headers=staff_headers).status_code == 200
    assert client.get(f"/clients/{hidden['id']}/notes", headers=staff_headers).status_code == 403
    assert client.post(
        f"/clients/{hidden['id']}/notes", json={"body": "hidden"}, headers=staff_headers
    ).status_code == 403
