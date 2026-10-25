"""Synthetic credentials must not travel into detailed errors and copied bug reports."""
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
import pytest
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.core.config import settings
from app.api.core.error_handlers import http_exception_handler

HEADERS = ["Authorization", "Cookie", "Stripe-Signature", "X-Apple-Token", "X-Google-Token", "X-Erl-Token",
    "X-Booking-Token", "X-Questionnaire-Token", "X-Portal-Token", "X-Waitlist-Token", "X-Payment-Token",
    "X-Gift-Token", "X-Visitor-Token", "X-Custom-Credential", "X-Login-Nonce", "X-Password-Hint",
    "X-Totp-Code", "X-Service-Secret"]


def app():
    application = FastAPI()
    application.add_exception_handler(StarletteHTTPException, http_exception_handler)
    @application.get("/probe")
    def probe():
        raise HTTPException(status_code=400, detail="Synthetic error")
    return application


@pytest.mark.parametrize("environment,detailed", [("test", True), ("development", False)])
@pytest.mark.parametrize("header", HEADERS)
def test_detailed_error_masks_existing_and_future_opaque_credential_headers(monkeypatch, header, environment, detailed):
    monkeypatch.setattr(settings, "environment", environment)
    monkeypatch.setattr(settings, "detailed_errors", detailed)
    synthetic = "synthetic-credential-never-a-real-token-" + header.lower()
    with TestClient(app()) as client:
        response = client.get("/probe", headers={header: synthetic, "X-Request-Id": "safe-request-reference"})
    assert response.status_code == 400
    request_headers = response.json()["error"]["request"]["headers"]
    assert request_headers[header.lower()] == "***"
    assert synthetic not in response.text
    assert request_headers["x-request-id"] == "safe-request-reference"


def test_production_without_detailed_errors_does_not_return_request_headers(monkeypatch):
    monkeypatch.setattr(settings, "environment", "production")
    monkeypatch.setattr(settings, "detailed_errors", False)
    synthetic = "synthetic-credential-never-real"
    with TestClient(app()) as client:
        response = client.get("/probe", headers={"X-Visitor-Token": synthetic})
    assert response.status_code == 400
    assert "request" not in response.json()["error"] and synthetic not in response.text
