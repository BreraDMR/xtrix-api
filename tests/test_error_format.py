"""Every error the API returns has the same shape: {"error": {"message", "status_code", ...}}."""


def _assert_common_shape(r, status):
    assert r.status_code == status, r.text
    body = r.json()
    assert "detail" not in body, body
    assert body["error"]["status_code"] == status
    assert isinstance(body["error"]["message"], str)


def test_unknown_path_is_404_in_common_format(client):
    _assert_common_shape(client.get("/nope"), 404)


def test_wrong_method_is_405_in_common_format(client, auth_headers):
    r = client.patch("/clients/", headers=auth_headers)
    _assert_common_shape(r, 405)
    # the Allow header from the router survives
    assert r.headers.get("allow")
