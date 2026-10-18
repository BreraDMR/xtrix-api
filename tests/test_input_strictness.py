"""Sweep over every POST/PUT body: values JSON clients send by mistake must get a clean 4xx.

- "   " where a real text is required (names) - used to be saved as an empty name
- null into a field the database needs - used to come back as 409 "duplicate value?"
- true instead of a number - used to be read as 1
- a number instead of a date (read as unix time) and dates centuries off
"""
import re
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.main import app
from tests.tenant_world import build_tenant
from tests.test_tenant_isolation import SPEC, _ids, _params, _resolve, _value

NAME_FIELDS = {"name", "full_name"}


def _cases():
    for path, ops in SPEC["paths"].items():
        if "webhooks" in path:
            continue
        for method, op in ops.items():
            content = op.get("requestBody", {}).get("content", {}).get("application/json")
            if method not in ("post", "put") or not content:
                continue
            schema = _resolve(content["schema"])
            yield method.upper(), path, op, schema


def _sweep(make_user, headers_for, make_values):
    client = TestClient(app, raise_server_exceptions=False)
    a = build_tenant(client, make_user, headers_for, "Alpha", "+42070011")
    ids = _ids(a)
    ids.update(ws_id=a["work"]["id"], block_id=a["block"]["id"])
    results = []
    n = 0
    for method, path, op, schema in _cases():
        props = schema.get("properties", {})
        base = {k: _value(k, v, ids) for k, v in props.items()}
        url = re.sub(r"\{(\w+)\}", lambda m: str(ids.get(m.group(1), 1)), path)
        # schedules belong to the solo specialist in the tenant world
        headers = a["specialist_headers"] if path.startswith("/schedules") else a["headers"]
        for field, field_schema in props.items():
            for value in make_values(field, _resolve(field_schema), field in schema.get("required", [])):
                n += 1
                unique = {k: v for k, v in (("email", f"s{n}@example.com"), ("phone", f"+42075{n:07d}"),
                                            ("contact_phone", f"+42076{n:07d}"), ("barcode", f"BC{n}"))
                          if k in props and k != field}
                # a fresh time slot every time, so a double booking can't pass for the error under test
                for k, v in props.items():
                    if k != field and _resolve(v).get("format") == "date-time" and isinstance(base.get(k), str):
                        unique[k] = (datetime.fromisoformat(base[k]) + timedelta(days=n)).isoformat()
                body = {**base, **unique, field: value}
                r = client.request(method, url, json=body, params=_params(op, ids), headers=headers)
                results.append((f"{method} {path} {field}={value!r}", r))
    return results


def _ok(status):
    return 200 <= status < 300


def test_blank_required_text_is_rejected(make_user, headers_for):
    def values(field, s, required):
        if s.get("type") == "string" and not field.endswith("_id") and "format" not in s and "pattern" not in s \
                and "enum" not in s and (required or s.get("minLength", 0) >= 1 or field in NAME_FIELDS):
            yield "   "
    bad = [case for case, r in _sweep(make_user, headers_for, values) if _ok(r.status_code)]
    assert not bad, "\n".join(bad)


def test_null_never_turns_into_409_or_500(make_user, headers_for):
    def values(field, s, required):
        if not field.endswith("_id"):
            yield None
    # a 409 for a taken time slot is fine, a 409 from a NOT NULL column is not
    bad = [f"{case}: {r.status_code}" for case, r in _sweep(make_user, headers_for, values)
           if r.status_code >= 500 or (r.status_code == 409 and r.json()["error"].get("type") == "IntegrityError")]
    assert not bad, "\n".join(bad)


def test_true_is_not_a_number(make_user, headers_for):
    def values(field, s, required):
        if s.get("type") in ("number", "integer") and not field.endswith("_id"):
            yield True
    bad = [case for case, r in _sweep(make_user, headers_for, values) if _ok(r.status_code)]
    assert not bad, "\n".join(bad)


def test_dates_are_iso_strings_in_a_sane_window(make_user, headers_for):
    def values(field, s, required):
        if s.get("format") == "date":
            yield from (12345, "1800-01-01", "9999-12-31")
        elif s.get("format") == "date-time":
            yield from (12345, "1800-01-01T10:00:00", "9999-12-31T10:00:00")
    bad = [case for case, r in _sweep(make_user, headers_for, values) if _ok(r.status_code)]
    assert not bad, "\n".join(bad)


@pytest.mark.parametrize("value, status", [("NaN", 422), ("XYZ", 422), ("czk", 200), ("CZK", 200)])
def test_currency_is_iso_4217(client, make_user, headers_for, value, status):
    owner = make_user("business_owner")
    r = client.post("/businesses/", json={"name": "Salon", "business_type": "salon", "currency": value},
                    headers=headers_for(owner))
    assert r.status_code == status, r.text
    if status == 200:
        assert r.json()["currency"] == "CZK"


def test_null_into_not_null_column_is_a_422_with_the_field(client, make_user, headers_for):
    owner = make_user("business_owner")
    h = headers_for(owner)
    biz = client.post("/businesses/", json={"name": "Salon", "business_type": "salon"}, headers=h).json()
    r = client.put(f"/businesses/{biz['id']}", json={"name": None}, headers=h)
    assert r.status_code == 422, r.text
    [err] = r.json()["error"]["detail"]["validation_errors"]
    assert err["field"] == "body -> name" and err["code"] == "null_not_allowed"
    assert client.get(f"/businesses/{biz['id']}", headers=h).json()["name"] == "Salon"


PAGING = {"page": [0, 10**20], "per_page": [0, 10**6], "skip": [-1, 10**20], "limit": [0, 10**6], "offset": [-1, 10**20]}


def test_paging_params_are_bounded(make_user, headers_for):
    client = TestClient(app, raise_server_exceptions=False)
    a = build_tenant(client, make_user, headers_for, "Alpha", "+42070011")
    ids = _ids(a)
    bad = []
    for path, ops in SPEC["paths"].items():
        op = ops.get("get")
        if not op:
            continue
        url = re.sub(r"\{(\w+)\}", lambda m: str(ids.get(m.group(1), 1)), path)
        for p in op.get("parameters", []):
            for junk in PAGING.get(p["name"], []) if p["in"] == "query" else []:
                # the solo specialist too - some lists (specialist profiles) aren't open to owners
                for headers in (a["headers"], a["specialist_headers"], {}):
                    r = client.get(url, params={**_params(op, ids), p["name"]: junk}, headers=headers)
                    # 401/403 is fine too: the role check runs before the query is looked at
                    if r.status_code not in (422, 401, 403):
                        bad.append(f"GET {path} {p['name']}={junk}: {r.status_code}")
    assert not bad, "\n".join(bad)


def test_true_is_not_a_quantity_inside_used_products(make_user, headers_for):
    client = TestClient(app, raise_server_exceptions=False)
    a = build_tenant(client, make_user, headers_for, "Alpha", "+42070011")
    start = (datetime.utcnow() + timedelta(days=5)).replace(hour=12, minute=0, second=0, microsecond=0)
    r = client.post("/visits/", json={
        "client_id": a["client"]["id"], "specialist_id": a["owner"].id, "service_id": a["service"]["id"],
        "date_start": start.isoformat(), "used_products": [{"product_id": a["product"]["id"], "quantity": True}],
    }, headers=a["headers"])
    assert r.status_code == 422, r.text
    err = r.json()["error"]["detail"]["validation_errors"][0]
    assert err["field"] == "body -> used_products -> 0 -> quantity" and err["code"] == "number_type"
