import json

import stripe
from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)


def test_stripe_webhook_valid(monkeypatch):
    settings_patch(monkeypatch, stripe_webhook_secret="whsec_test")

    def mock_construct_event(payload, sig_header, secret):
        assert secret == "whsec_test"
        return {"data": {"object": {"id": "evt_1", "status": "active"}}}

    monkeypatch.setattr(stripe.Webhook, "construct_event", mock_construct_event)
    resp = client.post(
        "/subscriptions/webhooks/stripe",
        data=b"{}",
        headers={"Stripe-Signature": "t=test,v1=abc"},
    )
    assert resp.status_code == 200
    assert resp.json()["received"] is True


def test_stripe_webhook_invalid(monkeypatch):
    settings_patch(monkeypatch, stripe_webhook_secret="whsec_test")

    def mock_construct_event(payload, sig_header, secret):
        raise ValueError("bad")

    monkeypatch.setattr(stripe.Webhook, "construct_event", mock_construct_event)
    resp = client.post(
        "/subscriptions/webhooks/stripe",
        data=b"{}",
        headers={"Stripe-Signature": "t=test,v1=abc"},
    )
    assert resp.status_code == 400


def test_apple_webhook_token(monkeypatch):
    settings_patch(monkeypatch, apple_webhook_token="secret_token")
    resp = client.post(
        "/subscriptions/webhooks/apple",
        json={"transaction_id": "tx1", "status": "active"},
        headers={"X-Apple-Token": "wrong"},
    )
    assert resp.status_code == 400
    resp = client.post(
        "/subscriptions/webhooks/apple",
        json={"transaction_id": "tx1", "status": "active"},
        headers={"X-Apple-Token": "secret_token"},
    )
    assert resp.status_code == 200


def test_google_webhook_token(monkeypatch):
    settings_patch(monkeypatch, google_webhook_token="gsecret")
    resp = client.post(
        "/subscriptions/webhooks/google",
        json={"purchase_token": "p1", "status": "active"},
        headers={"X-Google-Token": "wrong"},
    )
    assert resp.status_code == 400
    resp = client.post(
        "/subscriptions/webhooks/google",
        json={"purchase_token": "p1", "status": "active"},
        headers={"X-Google-Token": "gsecret"},
    )
    assert resp.status_code == 200


def settings_patch(monkeypatch, **kwargs):
    from app.api.core import config

    for k, v in kwargs.items():
        monkeypatch.setattr(config.settings, k, v)











