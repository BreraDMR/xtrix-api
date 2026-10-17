"""Tenant isolation sweep over the whole API.

Tenant A ("Alpha") and tenant B ("Bravo") are built through the API. Then every operation in
the OpenAPI schema is called as B's users with A's ids plugged into path, query and body.
Nothing of A may come back (the "Alpha" marker), and nothing aimed at A's objects may succeed.
"""
import io
import re
from datetime import date, datetime, timedelta

import pytest

from app.main import app
from tests.tenant_world import MARKER, build_tenant

SPEC = app.openapi()

# POSTs that only read (the body is just a query) - checked for leaks like GETs, not as writes
READ_ONLY_POSTS = {("POST", "/analytics/reports/export")}

# endpoints where a 2xx with A's id is fine because the resource isn't tenant data
NOT_TENANT_DATA = {
    ("GET", "/subscriptions/plans"),
    ("PUT", "/subscriptions/plans/{plan_id}"),  # admin only anyway
    ("GET", "/subscriptions/checkout/session/{session_id}"),
    # owner_id in the body is ignored by the API (see test_clients: owner_id_in_body_is_ignored)
    ("POST", "/clients/"),
}


def _ids(a):
    return {
        "client_id": a["client"]["id"], "service_id": a["service"]["id"], "visit_id": a["visit"]["id"],
        "product_id": a["product"]["id"], "category_id": a["category"]["id"], "expense_id": a["expense"]["id"],
        "ws_id": a["work"]["id"], "block_id": a["block"]["id"], "level_id": a["level"]["id"],
        "promotion_id": a["promotion"]["id"], "program_id": a["program"]["id"],
        "business_id": a["business"]["id"], "employee_id": a["employee"]["id"],
        "salary_id": a["salary"]["id"], "adjustment_id": a["adjustment"]["id"], "payment_id": a["payment"]["id"],
        "specialist_id": a["owner"].id, "owner_id": a["owner"].id, "business_owner_id": a["owner"].id,
        "user_id": a["owner"].id, "employee_user_id": a["employee_user_id"], "used_by_id": a["owner"].id,
        "entity_id": a["client"]["id"], "subscription_id": 1, "plan_id": 1, "session_id": "cs_test",
    }


def _resolve(schema):
    while "$ref" in schema:
        schema = SPEC["components"]["schemas"][schema["$ref"].split("/")[-1]]
    if "anyOf" in schema:
        non_null = [s for s in schema["anyOf"] if s.get("type") != "null"]
        return _resolve(non_null[0]) if non_null else {}
    if "allOf" in schema:
        return _resolve(schema["allOf"][0])
    return schema


# realistic values for fields the validators are picky about
NAMED = {
    "role": "specialist", "password": "Str0ng!Pass-word", "gender": "female", "status": "planned",
    "payment_method": "cash", "timezone": "Europe/Prague", "currency": "EUR", "color": "#112233",
    "totp_token": "123456", "token": "123456", "type": "bonus", "report_type": "financial",
}


def _value(name, schema, ids):
    schema = _resolve(schema)
    t, fmt = schema.get("type"), schema.get("format")
    if name in ids and not name.endswith("_ids"):
        return ids[name]
    if name.endswith("_ids"):
        id_list = [ids.get(name[:-1], 1)]
        return str(id_list) if t == "string" else id_list
    if "enum" in schema:
        return schema["enum"][0]
    if name in NAMED and t in ("string", None) and "pattern" not in schema:
        return NAMED[name]
    if fmt == "date-time":
        start = (datetime.utcnow() + timedelta(days=3)).replace(hour=10, minute=0, second=0, microsecond=0)
        return (start + timedelta(hours=1) if "end" in name else start).isoformat()
    if fmt == "date":
        return (date.today() + timedelta(days=30 if "end" in name else 0)).isoformat()
    if t == "string":
        m = re.match(r"^\^\(([^|)]+)", schema.get("pattern", ""))
        if m:
            return m.group(1)
        if "time" in name:
            return "17:00" if "end" in name else "09:00"
        if "email" in name:
            return "bravo.x@example.com"
        if "phone" in name:
            return "+420700999999"
        return "Bravo x"
    if t == "integer":
        return max(1, schema.get("minimum", 1), schema.get("exclusiveMinimum", 0) + 1)
    if t == "number":
        return float(max(1, schema.get("minimum", 1)))
    if t == "boolean":
        return True
    if t == "array":
        return []
    if t == "object":
        return {}
    return None

def _body(op, ids):
    content = op.get("requestBody", {}).get("content", {})
    if "application/json" not in content:
        return None
    schema = _resolve(content["application/json"]["schema"])
    props = schema.get("properties", {})
    # fill everything we know a sensible value for, so validation doesn't hide an authz hole
    return {k: _value(k, v, ids) for k, v in props.items() if k in schema.get("required", []) or k in ids}


def _params(op, ids):
    out = {}
    for p in op.get("parameters", []):
        if p["in"] != "query":
            continue
        if p["name"] in ids:
            out[p["name"]] = ids[p["name"]]
        elif p.get("required"):
            out[p["name"]] = _value(p["name"], p.get("schema", {}), ids)
    return out


def _text(r):
    ctype = r.headers.get("content-type", "")
    if "spreadsheet" in ctype or "excel" in ctype:
        from openpyxl import load_workbook

        wb = load_workbook(io.BytesIO(r.content))
        return " ".join(str(c) for ws in wb for row in ws.iter_rows(values_only=True) for c in row if c is not None)
    return r.text


OPERATIONS = sorted(
    (method.upper(), path)
    for path, ops in SPEC["paths"].items()
    for method in ops
    if not path.startswith("/subscriptions/webhooks")
)


@pytest.fixture
def world(client, make_user, headers_for):
    a = build_tenant(client, make_user, headers_for, "Alpha", "+42070011")
    b = build_tenant(client, make_user, headers_for, "Bravo", "+42070022")
    return a, b


def _call(client, method, path, ids, headers):
    op = SPEC["paths"][path][method.lower()]
    url = re.sub(r"\{(\w+)\}", lambda m: str(ids.get(m.group(1), 999999)), path)
    kwargs = {"headers": headers, "params": _params(op, ids)}
    body = _body(op, ids)
    if body is not None:
        kwargs["json"] = body
    return client.request(method, url, **kwargs)


def test_tenant_isolation_sweep(world, db, make_user, headers_for):
    from fastapi.testclient import TestClient

    from app.db.models.user import User

    client = TestClient(app, raise_server_exceptions=False)
    a, b = world
    ids = _ids(a)
    actors = {
        "B owner": b["headers"],
        "B specialist": b["specialist_headers"],
        "B employee": headers_for(db.get(User, b["employee_user_id"])),
        # a fresh signup without a business yet - several checks used to fall through to True for it
        "owner w/o business": headers_for(make_user("business_owner")),
    }
    leaks, writes, inconclusive, crashes = [], [], [], []

    # positive control: the ids really are A's live objects, otherwise the sweep proves nothing
    own_urls = [f"/clients/{ids['client_id']}", f"/services/{ids['service_id']}", f"/visits/{ids['visit_id']}",
                f"/products/{ids['product_id']}", f"/expenses/{ids['expense_id']}",
                f"/employees/{ids['employee_id']}", f"/businesses/{ids['business_id']}"]
    before = {}
    for url in own_urls:
        r = client.get(url, headers=a["headers"])
        assert r.status_code == 200, f"control {url}: {r.status_code} {r.text[:200]}"
        before[url] = r.json()

    # reads first, so a broken write check can't hide a read leak
    ordered = sorted(OPERATIONS, key=lambda op: op[0] != "GET")
    for method, path in ordered:
        if (method, path) in NOT_TENANT_DATA:
            continue
        touches_a = "{" in path or any(k in ids for k in _params(SPEC["paths"][path][method.lower()], ids)) \
            or any(k in ids for k in (_body(SPEC["paths"][path][method.lower()], ids) or {}))
        for actor, headers in actors.items():
            r = _call(client, method, path, ids, headers)
            if r.status_code == 500:
                crashes.append(f"{method} {path} as {actor}: 500 {r.text[:160]}")
                continue
            if r.status_code == 422:
                inconclusive.append(f"{method} {path} as {actor}: 422 {r.text[:160]}")
                continue
            if 200 <= r.status_code < 300:
                if MARKER in _text(r):
                    leaks.append(f"{method} {path} as {actor}: {r.status_code} leaks {MARKER} data")
                elif method != "GET" and (method, path) not in READ_ONLY_POSTS and touches_a:
                    writes.append(f"{method} {path} as {actor}: {r.status_code} accepted with A's ids")

    # and nothing B did changed or removed A's data
    for url, snapshot in before.items():
        r = client.get(url, headers=a["headers"])
        if r.status_code != 200 or r.json() != snapshot:
            writes.append(f"A's {url} changed during the sweep: {r.status_code}")

    report = "\n".join(
        ["LEAKS:"] + leaks + ["", "WRITES ACCEPTED:"] + writes + ["", "CRASHES:"] + crashes
        + ["", "INCONCLUSIVE (422):"] + inconclusive
    )
    print(report)
    assert not leaks and not writes and not crashes, report
