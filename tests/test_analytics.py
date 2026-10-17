from datetime import datetime, timedelta

from app.db.models.visit import Visit
from tests.tenant_world import MARKER


def _visit(db, client_id, specialist_id, price, status="completed"):
    start = datetime.utcnow() - timedelta(days=1)
    db.add(Visit(client_id=client_id, specialist_id=specialist_id, date_start=start,
                 date_end=start + timedelta(hours=1), price=price, status=status))
    db.commit()


def test_client_statistics_sums_completed_visits(client, db, test_user, auth_headers):
    r = client.post("/clients/", json={"full_name": "Jana", "contact_phone": "+420777000333"}, headers=auth_headers)
    client_id = r.json()["id"]
    _visit(db, client_id, test_user.id, 500)
    _visit(db, client_id, test_user.id, 300)
    _visit(db, client_id, test_user.id, 999, status="cancelled")

    r = client.get("/analytics/clients/statistics", headers=auth_headers)

    assert r.status_code == 200, r.text
    data = r.json()
    assert data["total_clients"] == 1
    assert data["average_spending"] == 800
    assert data["top_spenders"] == [{"client_id": client_id, "client_name": "Jana", "spending": 800.0}]


def test_analytics_are_scoped_to_own_business(client, make_user, headers_for):
    from tests.tenant_world import build_tenant

    a = build_tenant(client, make_user, headers_for, "Alpha", "+42070011")
    b = build_tenant(client, make_user, headers_for, "Bravo", "+42070022")
    for t, price in ((a, 700), (b, 300)):
        r = client.put(f"/visits/{t['visit']['id']}", json={"status": "completed", "price": price, "payment_method": "cash"},
                       headers=t["headers"])
        assert r.status_code == 200, r.text

    # A first, so a shared cache entry would show up in B's answer
    assert client.get("/analytics/financial/report", headers=a["headers"]).json()["total_revenue"] == 700
    assert client.get("/analytics/financial/report", headers=b["headers"]).json()["total_revenue"] == 300
    assert client.get("/analytics/visits/statistics", headers=b["headers"]).json()["total_visits"] == 1
    paid = client.get("/analytics/payments/statistics", headers=b["headers"]).json()
    assert MARKER not in str(paid) and "700" not in str(paid)
    # a solo specialist with no visits sees nothing of either business
    solo = headers_for(make_user("specialist"))
    assert client.get("/analytics/financial/report", headers=solo).json()["total_revenue"] == 0
    assert client.get("/analytics/trends", headers=solo).json()["current_value"] == 0
