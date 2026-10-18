"""Reports keep their JSON, but now the contract says what is in it."""
from datetime import datetime

from tests.tenant_world import build_tenant


def _schema(name):
    from app.main import app
    return app.openapi()["components"]["schemas"][name]


def _response_ref(path, method="get"):
    from app.main import app
    content = app.openapi()["paths"][path][method]["responses"]["200"]["content"]["application/json"]
    return content["schema"]["$ref"].rsplit("/", 1)[-1]


def test_cohort_table_rows_are_cohort_data_in_the_contract():
    items = _schema("CohortTableResponse")["properties"]["cohort_table"]["items"]
    assert items["$ref"].endswith("/CohortData")


def test_cohort_rows_keep_their_shape(client, make_user, headers_for, db):
    from app.db.models.visit import Visit
    a = build_tenant(client, make_user, headers_for, "Alpha", "+42070011")
    biz, owner, cl = a["business"]["id"], a["owner"], a["client"]["id"]
    for day in (datetime(2026, 1, 10, 10), datetime(2026, 2, 15, 10)):
        db.add(Visit(client_id=cl, specialist_id=owner.id, business_id=biz, date_start=day, status="completed", price=10))
    db.commit()

    r = client.get("/analytics/retention/cohorts", headers=a["headers"])
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total_cohorts"] == 1
    expected = {"cohort_month": "2026-01", "total_clients": 1, **{f"month_{i}": 0.0 for i in range(12)}}
    expected["month_1"] = 100.0
    assert body["cohort_table"] == [expected]


def test_profit_report_has_a_model():
    ref = _response_ref("/expenses/reports/profit")
    props = _schema(ref)["properties"]
    assert set(props) == {"period", "revenue", "expenses", "profit", "profit_margin", "expenses_by_category", "daily"}
    assert set(_schema(props["period"]["$ref"].rsplit("/", 1)[-1])["properties"]) == {"from", "to"}
    cat = _schema(props["expenses_by_category"]["items"]["$ref"].rsplit("/", 1)[-1])
    assert set(cat["properties"]) == {"category_id", "category_name", "category_code", "color", "total"}
    day = _schema(props["daily"]["items"]["$ref"].rsplit("/", 1)[-1])
    assert set(day["properties"]) == {"date", "revenue", "expenses", "profit"}


def test_profit_report_keeps_its_shape(client, make_user, headers_for, db):
    from app.db.models.visit import Visit
    a = build_tenant(client, make_user, headers_for, "Alpha", "+42070011")
    biz, owner, cl = a["business"]["id"], a["owner"], a["client"]["id"]
    db.add(Visit(client_id=cl, specialist_id=owner.id, business_id=biz,
                 date_start=datetime(2026, 3, 5, 10), status="completed", price=200))
    db.commit()
    from app.db.models.expense import Expense
    exp = db.get(Expense, a["expense"]["id"])
    exp.expense_date = datetime(2026, 3, 5, 12)
    db.commit()

    r = client.get("/expenses/reports/profit", params={
        "date_from": "2026-03-01T00:00:00Z", "date_to": "2026-03-31T23:59:59Z"}, headers=a["headers"])
    assert r.status_code == 200, r.text
    assert r.json() == {
        "period": {"from": "2026-03-01T00:00:00Z", "to": "2026-03-31T23:59:59Z"},
        "revenue": 200.0,
        "expenses": 123.0,
        "profit": 77.0,
        "profit_margin": 38.5,
        "expenses_by_category": [{
            "category_id": a["category"]["id"], "category_name": "Alpha Rent",
            "category_code": a["category"].get("code"), "color": a["category"].get("color"), "total": 123.0,
        }],
        "daily": [{"date": "2026-03-05", "revenue": 200.0, "expenses": 123.0, "profit": 77.0}],
    }


def test_expenses_by_category_report_has_a_model():
    from app.main import app
    schema = app.openapi()["paths"]["/expenses/reports/expenses-by-category"]["get"]["responses"]["200"]["content"]["application/json"]["schema"]
    assert schema["type"] == "array" and "$ref" in schema["items"]
