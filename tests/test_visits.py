from datetime import datetime, timedelta

from tests.tenant_world import build_tenant


def _visit_body(a, **overrides):
    start = (datetime.utcnow() + timedelta(days=5)).replace(microsecond=0)
    body = {
        "client_id": a["client"]["id"], "specialist_id": a["owner"].id, "service_id": a["service"]["id"],
        "date_start": start.isoformat(), "date_end": (start + timedelta(hours=1)).isoformat(), "price": 100,
    }
    body.update(overrides)
    return body


def test_owner_creates_visit_for_own_client(client, make_user, headers_for):
    a = build_tenant(client, make_user, headers_for, "Alpha", "+42070011")
    r = client.post("/visits/", json=_visit_body(a), headers=a["headers"])
    assert r.status_code == 200, r.text


def test_cannot_book_someone_elses_client_specialist_or_service(client, make_user, headers_for):
    a = build_tenant(client, make_user, headers_for, "Alpha", "+42070011")
    b = build_tenant(client, make_user, headers_for, "Bravo", "+42070022")
    own = _visit_body(b, specialist_id=b["owner"].id)

    for field, foreign in [("client_id", a["client"]["id"]), ("specialist_id", a["owner"].id),
                           ("service_id", a["service"]["id"])]:
        r = client.post("/visits/", json={**own, field: foreign}, headers=b["headers"])
        assert r.status_code == 403, f"{field}: {r.status_code} {r.text[:200]}"

    # and the attempt didn't quietly attach A's client to B
    assert client.get(f"/clients/{a['client']['id']}", headers=b["headers"]).status_code == 403


def test_cannot_switch_visit_to_someone_elses_service(client, make_user, headers_for):
    a = build_tenant(client, make_user, headers_for, "Alpha", "+42070011")
    b = build_tenant(client, make_user, headers_for, "Bravo", "+42070022")
    r = client.put(f"/visits/{b['visit']['id']}", json={"service_id": a["service"]["id"]}, headers=b["headers"])
    assert r.status_code == 403
