import pytest

# collections are declared as "/clients/" etc. A request without the slash used to get a 307
# to an absolute http:// URL of the API host - behind a TLS proxy or another origin the
# browser then drops the Authorization header. Both spellings must just work, no redirect.


@pytest.mark.parametrize("path", ["/clients", "/clients/"])
def test_create_client_with_or_without_slash(client, auth_headers, path):
    r = client.post(path, json={"full_name": "Jana"}, headers=auth_headers, follow_redirects=False)
    assert r.status_code == 200, r.text[:300]
    assert r.json()["full_name"] == "Jana"


@pytest.mark.parametrize("path", ["/visits", "/services", "/clients", "/users/me/", "/clients/search/"])
def test_no_redirects_on_slash_mismatch(client, auth_headers, path):
    r = client.get(path, headers=auth_headers, follow_redirects=False)
    assert r.status_code not in (301, 302, 307, 308), r.headers.get("location")


def test_unknown_path_is_still_404(client, auth_headers):
    r = client.get("/no-such-thing", headers=auth_headers, follow_redirects=False)
    assert r.status_code == 404
