"""Staff made through create-with-user get role "specialist", so their checkboxes have to decide
what they may do - not the role. A solo specialist (no Employee record) keeps working as before."""
from datetime import datetime, timedelta

import pytest

from app.db.models.user import User

NO_RIGHTS = {"can_create_visits": False, "can_edit_clients": False,
             "can_view_financials": False, "can_manage_services": False}
ALL_RIGHTS = {k: True for k in NO_RIGHTS}


@pytest.fixture
def salon(client, db, make_user, headers_for):
    owner = make_user("business_owner")
    h = headers_for(owner)
    business = client.post("/businesses/", json={"name": "Perm Salon", "business_type": "salon"}, headers=h).json()
    r = client.post("/employees/create-with-user", json={
        "full_name": "Staff Member", "email": "staff.member@example.com", "base_salary": 0,
    }, headers=h)
    assert r.status_code == 200, r.text
    employee = r.json()
    emp_user = db.get(User, employee["employee_user_id"])
    client_row = client.post("/clients/", json={"full_name": "Perm Client"}, headers=h).json()
    return {"owner": owner, "h": h, "business": business, "employee": employee,
            "emp_user": emp_user, "eh": headers_for(emp_user), "client": client_row}


def _set_rights(client, salon, rights):
    r = client.put(f"/employees/{salon['employee']['id']}", json={"permissions": rights}, headers=salon["h"])
    assert r.status_code == 200, r.text


def _visit_body(salon):
    start = (datetime.utcnow() + timedelta(days=5)).replace(hour=11, minute=0, second=0, microsecond=0)
    return {"client_id": salon["client"]["id"], "specialist_id": salon["emp_user"].id,
            "date_start": start.isoformat(), "date_end": (start + timedelta(hours=1)).isoformat()}


def test_staff_without_checkboxes_is_refused(client, salon):
    _set_rights(client, salon, NO_RIGHTS)
    eh = salon["eh"]
    calls = {
        "create client": client.post("/clients/", json={"full_name": "Nope"}, headers=eh),
        "create visit": client.post("/visits/", json=_visit_body(salon), headers=eh),
        "create service": client.post("/services/", json={
            "name": "Nope", "price": 1, "duration_minutes": 30, "specialist_id": salon["emp_user"].id,
        }, headers=eh),
        "list expenses": client.get("/expenses/", headers=eh),
        "create expense": client.post("/expenses/", json={
            "amount": 5, "expense_date": datetime.utcnow().isoformat(), "business_id": salon["business"]["id"],
        }, headers=eh),
        "profit report": client.get("/expenses/reports/profit", headers=eh),
        "expenses by category": client.get("/expenses/reports/expenses-by-category", headers=eh),
        "financial report": client.get("/analytics/financial/report", headers=eh),
        "rename business": client.put(f"/businesses/{salon['business']['id']}",
                                      json={"name": "Renamed by staff", "currency": "USD"}, headers=eh),
    }
    allowed = {what: r.status_code for what, r in calls.items() if r.status_code != 403}
    assert not allowed, f"staff with no checkboxes got through: {allowed}"


def test_staff_checkboxes_grant_rights(client, salon):
    _set_rights(client, salon, ALL_RIGHTS)
    eh = salon["eh"]
    assert client.post("/clients/", json={"full_name": "Allowed"}, headers=eh).status_code == 200
    assert client.post("/visits/", json=_visit_body(salon), headers=eh).status_code == 200
    assert client.post("/services/", json={
        "name": "Allowed", "price": 1, "duration_minutes": 30, "specialist_id": salon["emp_user"].id,
    }, headers=eh).status_code == 200
    assert client.get("/expenses/", headers=eh).status_code == 200
    assert client.get("/expenses/reports/profit", headers=eh).status_code == 200
    # editing the business itself is never a staff thing
    r = client.put(f"/businesses/{salon['business']['id']}", json={"name": "Renamed"}, headers=eh)
    assert r.status_code == 403


def test_owner_still_edits_own_business(client, salon):
    r = client.put(f"/businesses/{salon['business']['id']}", json={"name": "Owner Renamed"}, headers=salon["h"])
    assert r.status_code == 200 and r.json()["name"] == "Owner Renamed"


def test_fired_staff_loses_staff_rights(client, salon):
    _set_rights(client, salon, ALL_RIGHTS)
    r = client.delete(f"/employees/{salon['employee']['id']}", headers=salon["h"])
    assert r.status_code == 200, r.text
    eh = salon["eh"]
    assert client.get("/expenses/", headers=eh).status_code == 403
    # and they're out of the business: the owner's clients are gone for them
    assert client.get(f"/clients/{salon['client']['id']}", headers=eh).status_code in (403, 404)


def test_solo_specialist_unchanged(client, make_user, headers_for):
    spec = make_user("specialist")
    h = headers_for(spec)
    assert client.post("/clients/", json={"full_name": "Solo Client"}, headers=h).status_code == 200
    assert client.post("/services/", json={
        "name": "Solo", "price": 1, "duration_minutes": 30, "specialist_id": spec.id,
    }, headers=h).status_code == 200
    assert client.get("/analytics/financial/report", headers=h).status_code == 200
