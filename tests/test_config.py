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
