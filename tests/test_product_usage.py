from tests.tenant_world import build_tenant


def test_usage_takes_the_business_from_the_user(client, make_user, headers_for):
    a = build_tenant(client, make_user, headers_for, "Alpha", "+42070011")
    r = client.post("/products/usage", json={"product_id": a["product"]["id"], "quantity": 1}, headers=a["headers"])
    assert r.status_code == 200, r.text
    assert r.json()["business_id"] == a["business"]["id"]


def test_business_id_in_the_body_is_ignored(client, make_user, headers_for):
    a = build_tenant(client, make_user, headers_for, "Alpha", "+42070011")
    b = build_tenant(client, make_user, headers_for, "Beta", "+42070022")
    r = client.post("/products/usage", json={
        "product_id": b["product"]["id"], "quantity": 1, "business_id": a["business"]["id"],
    }, headers=b["headers"])
    assert r.status_code == 200, r.text
    assert r.json()["business_id"] == b["business"]["id"]


def test_products_used_on_a_new_visit_are_written_off(client, make_user, headers_for):
    from datetime import datetime, timedelta

    a = build_tenant(client, make_user, headers_for, "Alpha", "+42070011")
    start = (datetime.utcnow() + timedelta(days=5)).replace(hour=12, minute=0, second=0, microsecond=0)
    r = client.post("/visits/", json={
        "client_id": a["client"]["id"], "specialist_id": a["owner"].id, "service_id": a["service"]["id"],
        "date_start": start.isoformat(), "date_end": (start + timedelta(hours=1)).isoformat(),
        "used_products": [{"product_id": a["product"]["id"], "quantity": 2}],
    }, headers=a["headers"])
    assert r.status_code == 200, r.text
    usages = client.get("/products/usage", params={"visit_id": r.json()["id"]}, headers=a["headers"]).json()["items"]
    assert [(u["quantity"], u["business_id"]) for u in usages] == [(2, a["business"]["id"])]
