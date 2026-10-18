"""Datetimes in query strings go through the same UTC rule as bodies: an offset is converted
to naive UTC. Before, an aware date_from reached Postgres as timestamptz and got compared with
our naive UTC columns through the session time zone, and any Python code comparing it with
utcnow() would raise TypeError."""
import re
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.main import app
from tests.tenant_world import build_tenant
from tests.test_tenant_isolation import SPEC, _ids, _params, _resolve


def _datetime_params(op):
    return [p["name"] for p in op.get("parameters", [])
            if p["in"] == "query" and _resolve(p.get("schema", {})).get("format") == "date-time"]


def _ops_with_datetime_params():
    for path, ops in SPEC["paths"].items():
        op = ops.get("get")
        if op and _datetime_params(op):
            yield path, op


def test_there_are_datetime_query_params_to_check():
    paths = [p for p, _ in _ops_with_datetime_params()]
    assert any(p.startswith("/analytics") for p in paths) and "/visits/" in paths


@pytest.mark.parametrize("suffix", ["Z", "+02:00"])
def test_aware_datetimes_in_queries_never_500(make_user, headers_for, suffix):
    client = TestClient(app, raise_server_exceptions=False)
    a = build_tenant(client, make_user, headers_for, "Alpha", "+42070011")
    admin = headers_for(make_user("admin", email="admin.q@example.com"))
    ids = _ids(a)
    now = datetime.utcnow().replace(microsecond=0)
    # a past and a future bound, so code comparing them with "now" runs both ways
    past, future = (now - timedelta(days=30)).isoformat() + suffix, (now + timedelta(days=30)).isoformat() + suffix
    bad = []
    for path, op in _ops_with_datetime_params():
        url = re.sub(r"\{(\w+)\}", lambda m: str(ids.get(m.group(1), 1)), path)
        names = _datetime_params(op)
        base = {k: v for k, v in _params(op, ids).items() if k not in names}
        both = {name: past if i == 0 else future for i, name in enumerate(names)}
        # each one alone too: the missing bound is often filled with a naive utcnow()
        variants = [both] + [{name: value} for name in names for value in (past, future)]
        for extra in variants:
            for headers in (a["headers"], a["specialist_headers"], admin):
                r = client.get(url, params={**base, **extra}, headers=headers)
                if r.status_code >= 500:
                    bad.append(f"GET {path} {extra}: {r.status_code} {r.text[:200]}")
    assert not bad, "\n".join(bad)


@pytest.fixture
def db_session_not_in_utc():
    # psycopg sends an aware datetime as timestamptz, and Postgres compares that with our naive
    # columns through the session time zone - only harmless while the server happens to be on UTC
    from app.db.session import engine
    name = engine.url.database
    with engine.begin() as conn:
        conn.execute(text(f'ALTER DATABASE "{name}" SET timezone TO \'Asia/Tokyo\''))
    engine.dispose()
    yield
    with engine.begin() as conn:
        conn.execute(text(f'ALTER DATABASE "{name}" RESET timezone'))
    engine.dispose()


@pytest.mark.parametrize("suffix,hours", [("Z", 0), ("+02:00", 2)])
def test_an_offset_in_a_query_means_the_same_instant(client, make_user, headers_for, db_session_not_in_utc,
                                                       suffix, hours):
    a = build_tenant(client, make_user, headers_for, "Alpha", "+42070011")
    visit_start = datetime.fromisoformat(a["visit"]["date_start"].rstrip("Z"))

    def listed(moment):
        local = (moment + timedelta(hours=hours)).isoformat() + suffix
        r = client.get("/visits/", params={"date_from": local}, headers=a["headers"])
        assert r.status_code == 200, r.text
        return a["visit"]["id"] in [v["id"] for v in r.json()["items"]]

    assert listed(visit_start - timedelta(minutes=30))
    assert not listed(visit_start + timedelta(minutes=30))
