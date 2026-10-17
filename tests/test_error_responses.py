from app.api.core import config


def test_detailed_errors_are_off_by_default():
    assert config.Settings.model_fields["detailed_errors"].default is False


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
