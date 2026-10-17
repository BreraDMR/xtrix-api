"""Fool-proofing sweep: every POST/PUT body field gets a junk value, the API must say 4xx.

Junk = negative / absurdly large numbers and 20k-char strings. Empty strings are allowed
through on purpose: for optional text fields "" just means "clear it".
"""
import re

from fastapi.testclient import TestClient

from app.main import app
from tests.tenant_world import build_tenant
from tests.test_tenant_isolation import SPEC, _ids, _params, _resolve, _value

JUNK = {"number": [-100.0, 1e12], "integer": [-5, 2**40], "string": ["x" * 20000]}

# fields where a negative or huge value is legitimate
ALLOWED = {
    ("POST", "/employees/{employee_id}/salary/{salary_id}/adjustments", "amount"),  # penalties are negative
    ("POST", "/clients/", "location_lon"),  # -100 is a real longitude
    ("PUT", "/clients/{client_id}", "location_lon"),
}


def test_junk_input_is_rejected(make_user, headers_for):
    client = TestClient(app, raise_server_exceptions=False)
    a = build_tenant(client, make_user, headers_for, "Alpha", "+42070011")
    ids = _ids(a)
    problems = []
    for path, ops in SPEC["paths"].items():
        if "webhooks" in path:
            continue
        for method, op in ops.items():
            content = op.get("requestBody", {}).get("content", {}).get("application/json")
            if method not in ("post", "put") or not content:
                continue
            props = _resolve(content["schema"]).get("properties", {})
            base = {k: _value(k, v, ids) for k, v in props.items()}
            url = re.sub(r"\{(\w+)\}", lambda m: str(ids.get(m.group(1), 1)), path)
            for field, field_schema in props.items():
                kind = _resolve(field_schema).get("type")
                if field.endswith(("_id", "_ids")) or kind not in JUNK or (method.upper(), path, field) in ALLOWED:
                    continue
                for junk in JUNK[kind]:
                    n = len(problems) * 1000 + hash((path, field, str(junk))) % 1000
                    # fresh email/phone each time, so "already registered" can't mask the field under test
                    unique = {k: v for k, v in (("email", f"u{n}@example.com"), ("phone", f"+42073{n:07d}"),
                                                ("contact_phone", f"+42074{n:07d}")) if k in props}
                    body = {**base, **unique, field: junk}
                    r = client.request(method.upper(), url, json=body, params=_params(op, ids), headers=a["headers"])
                    if r.status_code >= 500 or 200 <= r.status_code < 300:
                        problems.append(f"{method.upper()} {path} {field}={str(junk)[:12]!r}: {r.status_code}")
    assert not problems, "\n".join(problems)


def test_duplicate_value_is_a_clean_409(client, make_user, headers_for):
    a = build_tenant(client, make_user, headers_for, "Alpha", "+42070011")
    biz = a["business"]["id"]
    one = client.post("/products/", json={"name": "One", "business_id": biz, "barcode": "123"}, headers=a["headers"])
    two = client.post("/products/", json={"name": "Two", "business_id": biz, "barcode": "456"}, headers=a["headers"])
    assert one.status_code == 200 and two.status_code == 200, (one.text, two.text)

    r = client.put(f"/products/{two.json()['id']}", json={"barcode": "123"}, headers=a["headers"])

    assert r.status_code == 409, r.text
    assert "psycopg2" not in r.text and "ix_" not in r.text


def test_promotion_cannot_end_before_it_starts(client, make_user, headers_for):
    a = build_tenant(client, make_user, headers_for, "Alpha", "+42070011")
    body = {"name": "Backwards", "promotion_type": "bonus_points", "program_id": a["program"]["id"],
            "start_date": "2030-02-01", "end_date": "2030-01-01"}
    assert client.post("/loyalty/promotions", json=body, headers=a["headers"]).status_code == 422
