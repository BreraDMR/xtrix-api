import re

import pytest

from app.main import app

# Smoke test: hit every GET route as each role and make sure nothing blows up with a 500.
# Path params get a made-up id, so most of these end in 404/403 - that's fine, we only want no crashes.

# app.routes only holds lazy wrappers for included routers in this FastAPI version,
# the OpenAPI schema is the reliable full list (and it's the contract anyway)
GET_ROUTES = sorted(path for path, ops in app.openapi()["paths"].items() if "get" in ops)


def _fill(path):
    return re.sub(r"\{[^}]+\}", "999999", path)


@pytest.fixture
def users_by_role(client, make_user, headers_for):
    owner = make_user("business_owner")
    owner_h = headers_for(owner)
    r = client.post("/businesses/", json={"name": "Smoke salon", "business_type": "salon"}, headers=owner_h)
    assert r.status_code == 200, r.text
    return {
        "admin": headers_for(make_user("admin")),
        "business_owner": owner_h,
        "specialist": headers_for(make_user("specialist")),
        "anonymous": {},
    }


@pytest.mark.parametrize("path", GET_ROUTES)
def test_get_route_does_not_crash(client, users_by_role, path):
    failures = []
    for role, headers in users_by_role.items():
        r = client.get(_fill(path), headers=headers)
        # 503 is a deliberate "dependency not configured" (e.g. no Stripe key in tests), 500 is a crash
        if r.status_code >= 500 and r.status_code != 503:
            failures.append(f"{role}: {r.status_code} {r.text[:200]}")
    assert not failures, "\n".join(failures)
