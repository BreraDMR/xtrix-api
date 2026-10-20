"""Client overview: history and stats for all staff, money only for those allowed.

The overview must not fan out into a query per visit, and a limited employee must
see the visits with prices hidden rather than a cut-down history.
"""
from datetime import datetime, timedelta

from sqlalchemy import event

from app.db.models.user import User


def _salon(client, make_user, headers_for, name="Overview", phone="+420700008001", currency="CZK"):
    owner = make_user("business_owner", full_name=f"{name} Owner", email=f"{name.lower()}.owner@example.com")
    headers = headers_for(owner)
    business = client.post(
        "/businesses/",
        json={"name": f"{name} Salon", "business_type": "salon", "currency": currency},
        headers=headers,
    )
    assert business.status_code == 200, business.text
    row = client.post(
        "/clients/",
        json={"full_name": f"{name} Client", "contact_phone": phone},
        headers=headers,
    )
    assert row.status_code == 200, row.text
    return {"owner": owner, "headers": headers, "business": business.json(), "client": row.json()}


def _create_visit(client, headers, salon, start, **fields):
    body = {
        "client_id": salon["client"]["id"],
        "specialist_id": salon["owner"].id,
        "date_start": start.replace(microsecond=0).isoformat(),
        "date_end": (start.replace(microsecond=0) + timedelta(hours=1)).isoformat(),
        "service_name": fields.pop("service_name", "Haircut"),
        **fields,
    }
    response = client.post("/visits/", json=body, headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def _overview(client, headers, salon):
    response = client.get(f"/clients/{salon['client']['id']}/overview", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def test_history_stats_next_visit_and_payments(client, make_user, headers_for):
    salon = _salon(client, make_user, headers_for)
    h = salon["headers"]
    now = datetime.utcnow().replace(microsecond=0)

    old = _create_visit(client, h, salon, now - timedelta(days=20),
                        status="completed", price=800, payment_method="cash")
    recent = _create_visit(client, h, salon, now - timedelta(days=10),
                           status="completed", price=1200, payment_method="card")
    cancelled = _create_visit(client, h, salon, now - timedelta(days=5), status="cancelled")
    next_visit = _create_visit(client, h, salon, now + timedelta(days=3))
    later = _create_visit(client, h, salon, now + timedelta(days=7))

    data = _overview(client, h, salon)

    assert data["client_id"] == salon["client"]["id"]
    assert data["stats"] == {
        "total_visits": 5,
        "completed_visits": 2,
        "cancelled_visits": 1,
        "last_visit_at": recent["date_start"],
        "next_visit_at": next_visit["date_start"],
    }
    assert data["next_visit"]["id"] == next_visit["id"]
    assert [item["id"] for item in data["upcoming_visits"]] == [next_visit["id"], later["id"]]
    assert [item["id"] for item in data["recent_visits"]] == [cancelled["id"], recent["id"], old["id"]]
    assert data["payments"] == {"currency": "CZK", "total_paid": 2000.0, "paid_visits": 2}

    paid = {item["id"]: item for item in data["recent_visits"]}
    assert paid[old["id"]]["price"] == 800
    assert paid[old["id"]]["payment_method"] == "cash"
    assert paid[cancelled["id"]]["price"] is None
    assert data["next_visit"]["service_name"] == "Haircut"
    assert data["next_visit"]["specialist_name"] == salon["owner"].full_name


def _count_selects(db, call):
    engine = db.get_bind()
    statements = []

    def before(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append(statement)

    event.listen(engine, "before_cursor_execute", before)
    try:
        response = call()
    finally:
        event.remove(engine, "before_cursor_execute", before)
    assert response.status_code == 200, response.text
    return len(statements)


def test_overview_does_not_query_per_visit(client, make_user, headers_for, db):
    salon = _salon(client, make_user, headers_for, "NoPlusOne", "+420700008002")
    h = salon["headers"]
    now = datetime.utcnow().replace(microsecond=0)
    for day in range(2):
        _create_visit(client, h, salon, now - timedelta(days=30 + day))

    def call():
        return client.get(f"/clients/{salon['client']['id']}/overview", headers=h)

    two_visits = _count_selects(db, call)
    for day in range(8):
        _create_visit(client, h, salon, now - timedelta(days=40 + day))
    ten_visits = _count_selects(db, call)

    assert ten_visits == two_visits, f"overview grew from {two_visits} to {ten_visits} selects"
    assert ten_visits <= 10
    assert len(_overview(client, h, salon)["recent_visits"]) == 10


def test_staff_sees_history_but_only_allowed_staff_sees_money(client, make_user, headers_for, db):
    salon = _salon(client, make_user, headers_for, "Staffview", "+420700008003")
    h = salon["headers"]
    now = datetime.utcnow().replace(microsecond=0)
    _create_visit(client, h, salon, now - timedelta(days=2), status="completed", price=500, payment_method="card")

    employee = client.post("/employees/create-with-user", json={
        "full_name": "Overview Staff", "email": "overview.staff@example.com",
    }, headers=h)
    assert employee.status_code == 200, employee.text
    employee_row = employee.json()
    staff_headers = headers_for(db.get(User, employee_row["employee_user_id"]))

    data = _overview(client, staff_headers, salon)
    assert data["stats"]["completed_visits"] == 1
    assert data["recent_visits"][0]["price"] is None
    assert data["recent_visits"][0]["payment_method"] is None
    assert data["payments"] is None

    allowed = client.put(
        f"/employees/{employee_row['id']}",
        json={"permissions": {"can_view_financials": True}},
        headers=h,
    )
    assert allowed.status_code == 200, allowed.text
    data = _overview(client, staff_headers, salon)
    assert data["recent_visits"][0]["price"] == 500
    assert data["recent_visits"][0]["payment_method"] == "card"
    assert data["payments"]["total_paid"] == 500

    other = _salon(client, make_user, headers_for, "Otherview", "+420700008004")
    assert client.get(
        f"/clients/{other['client']['id']}/overview", headers=staff_headers
    ).status_code == 403

    hidden = client.post("/clients/", json={"full_name": "Hidden Person"}, headers=h).json()
    client.put(
        f"/employees/{employee_row['id']}",
        json={"permissions": {"assigned_client_ids": [hidden["id"]]}},
        headers=h,
    )
    assert client.get(
        f"/clients/{salon['client']['id']}/overview", headers=staff_headers
    ).status_code == 403
    assert client.get(f"/clients/{hidden['id']}/overview", headers=staff_headers).status_code == 200


def test_solo_specialist_gets_their_own_overview(client, make_user, headers_for):
    solo = make_user("specialist", full_name="Solo Owner")
    other = make_user("specialist", full_name="Another Solo")
    h = headers_for(solo)
    created = client.post("/clients/", json={"full_name": "Solo Client"}, headers=h)
    assert created.status_code == 200, created.text
    salon = {"owner": solo, "headers": h, "client": created.json()}
    now = datetime.utcnow().replace(microsecond=0)
    _create_visit(client, h, salon, now - timedelta(days=1), status="completed", price=90, payment_method="cash")

    data = _overview(client, h, salon)
    assert data["stats"]["completed_visits"] == 1
    assert data["payments"] == {"currency": "EUR", "total_paid": 90.0, "paid_visits": 1}

    assert client.get(
        f"/clients/{salon['client']['id']}/overview", headers=headers_for(other)
    ).status_code == 403
