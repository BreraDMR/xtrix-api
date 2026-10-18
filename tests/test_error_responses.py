from app.api.core import config


def test_detailed_error_does_not_echo_secrets(client, monkeypatch, make_user, headers_for):
    monkeypatch.setattr(config.settings, "detailed_errors", True)
    user = make_user("specialist", email="e@example.com")
    headers = headers_for(user)

    r = client.post(
        "/users/login",
        json={"email": "e@example.com", "password": "Wr0ng!Secret-pass"},
        headers=headers,
    )

    assert r.status_code == 401
    text = r.text
    assert "Wr0ng!Secret-pass" not in text
    assert headers["Authorization"].split()[1] not in text



def test_form_login_is_422_without_echoing_credentials(client, monkeypatch):
    monkeypatch.setattr(config.settings, "detailed_errors", True)
    response = client.post("/users/login", data={
        "email": "synthetic@example.com", "password": "NeverEcho!123",
    })
    assert response.status_code == 422, response.text
    assert response.json()["error"]["type"] == "ValidationError"
    assert "NeverEcho!123" not in response.text


def test_invalid_json_credentials_are_not_in_validation_details(client, monkeypatch):
    monkeypatch.setattr(config.settings, "detailed_errors", True)
    response = client.post("/users/", json={
        "full_name": "Synthetic", "email": "synthetic@example.com",
        "password": "short-secret", "role": "specialist",
    })
    assert response.status_code == 422, response.text
    assert "short-secret" not in response.text


def test_non_utf8_request_is_422(client):
    response = client.post("/users/login", content=b"\xff\xfe",
                           headers={"Content-Type": "application/octet-stream"})
    assert response.status_code == 422, response.text
