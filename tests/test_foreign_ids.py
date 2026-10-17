"""Tenant B puts tenant A's ids into request bodies. Every such write must be refused, and must not
leave anything behind that points at A's data."""
from datetime import datetime, timedelta

import pytest

from tests.tenant_world import build_tenant


@pytest.fixture
def ab(client, make_user, headers_for):
    a = build_tenant(client, make_user, headers_for, "Alpha", "+42070011")
    b = build_tenant(client, make_user, headers_for, "Beta", "+42070022")
    return a, b


def _bodies(a, b):
    today = datetime.utcnow().date()
    start = (datetime.utcnow() + timedelta(days=9)).replace(hour=15, minute=0, second=0, microsecond=0)
    bb = b["business"]["id"]
    promo = {"name": "x", "promotion_type": "bonus_points", "program_id": b["program"]["id"],
             "start_date": today.isoformat(), "end_date": (today + timedelta(days=5)).isoformat()}
    return [
        ("expense with A's category", "post", "/expenses/", {
            "amount": 1, "expense_date": datetime.utcnow().isoformat(), "business_id": bb,
            "category_id": a["category"]["id"]}),
        ("expense moved to A's category", "put", f"/expenses/{b['expense']['id']}", {"category_id": a["category"]["id"]}),
        ("promotion for A's client", "post", "/loyalty/promotions", {**promo, "client_id": a["client"]["id"]}),
        ("promotion on A's program", "post", "/loyalty/promotions", {**promo, "program_id": a["program"]["id"]}),
        ("promotion moved to A's client", "put", f"/loyalty/promotions/{b['promotion']['id']}", {"client_id": a["client"]["id"]}),
        ("promotion on A's services", "post", "/loyalty/promotions", {**promo, "service_ids": str(a["service"]["id"])}),
        ("level on A's program", "post", "/loyalty/levels", {
            "name": "x", "level_number": 2, "min_spending": 0, "program_id": a["program"]["id"]}),
        ("usage of A's product", "post", "/products/usage", {
            "product_id": a["product"]["id"], "quantity": 1, "business_id": bb}),
        ("usage for A's client", "post", "/products/usage", {
            "product_id": b["product"]["id"], "quantity": 1, "business_id": bb, "client_id": a["client"]["id"]}),
        ("usage on A's visit", "post", "/products/usage", {
            "product_id": b["product"]["id"], "quantity": 1, "business_id": bb, "visit_id": a["visit"]["id"]}),
        ("usage by A's employee", "post", "/products/usage", {
            "product_id": b["product"]["id"], "quantity": 1, "business_id": bb, "used_by_id": a["employee_user_id"]}),
        ("product in A's business", "post", "/products/", {"name": "x", "business_id": a["business"]["id"]}),
        ("category in A's business", "post", "/expenses/categories", {"name": "x", "business_id": a["business"]["id"]}),
        ("program in A's business", "post", "/loyalty/program", {
            "program_type": "points", "business_id": a["business"]["id"], "name": "x"}),
        # owner_id on a new client is ignored on purpose (test_clients.test_owner_id_in_body_is_ignored)
        ("relation for A's client", "post", "/client-relations/", {
            "client_id": a["client"]["id"], "specialist_id": b["owner"].id, "business_id": bb}),
        ("visit with A's service", "put", f"/visits/{b['visit']['id']}", {"service_id": a["service"]["id"]}),
        ("visit for A's client", "post", "/visits/", {
            "client_id": a["client"]["id"], "specialist_id": b["owner"].id,
            "date_start": start.isoformat(), "date_end": (start + timedelta(hours=1)).isoformat()}),
        ("points on A's visit", "post", f"/loyalty/use-points?client_id={b['client']['id']}", {
            "points_to_use": 0, "visit_id": a["visit"]["id"]}),
        ("export of A's specialist", "post", "/analytics/reports/export", {
            "report_type": "visits", "format": "csv", "specialist_id": a["owner"].id}),
    ]


def test_foreign_ids_in_bodies_are_refused(client, ab):
    a, b = ab
    accepted = {}
    for what, method, url, body in _bodies(a, b):
        r = getattr(client, method)(url, json=body, headers=b["headers"])
        if r.status_code < 400:
            accepted[what] = f"{r.status_code} {r.text[:120]}"
    assert not accepted, "B wrote with A's ids:\n" + "\n".join(f"{k}: {v}" for k, v in accepted.items())


def test_foreign_loyalty_balance_is_not_found(client, ab):
    a, b = ab
    r = client.get(f"/loyalty/balance/{a['client']['id']}", headers=b["headers"])
    assert r.status_code in (403, 404), r.text
