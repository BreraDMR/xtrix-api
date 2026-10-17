import pytest

from app.api.core import config


def test_detailed_errors_are_off_by_default():
    assert config.Settings.model_fields["detailed_errors"].default is False


def test_production_refuses_placeholder_jwt_secret(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("JWT_SECRET", "change-me-minimum-32-characters-long")
    with pytest.raises(ValueError, match="placeholder"):
        config.Settings()
    monkeypatch.setenv("JWT_SECRET", "x" * 40)
    assert config.Settings().environment == "production"


def test_aws_keys_come_from_the_standard_env_names(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIA_TEST")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "secret_test")
    s = config.Settings(_env_file=None)
    assert (s.aws_access_key, s.aws_secret_key) == ("AKIA_TEST", "secret_test")
