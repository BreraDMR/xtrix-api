import pytest

# these used to be swallowed by "/{id}" routes declared above them and always answered 422
STATIC_ROUTES = [
    "/employees/me/salary/current",
    "/employees/me/salary/history",
    "/visits/next_slot?specialist_id=1",
    "/visits/reminders",
    "/products/usage",
]


@pytest.mark.parametrize("url", STATIC_ROUTES)
def test_static_route_is_not_shadowed(client, auth_headers, url):
    r = client.get(url, headers=auth_headers)
    assert "path ->" not in r.text, r.text[:300]
