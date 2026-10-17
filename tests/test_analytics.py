from datetime import datetime, timedelta

from app.db.models.visit import Visit


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
