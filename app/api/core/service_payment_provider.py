"""Payment provider boundary for guest deposits.

The sandbox adapter is intentionally the only implementation in this release: it signs
events with an HMAC and redirects to our own checkout screen, so the full webhook and
state-machine path can be exercised without a live merchant account or real money.
Live providers are blocked by D04 and by the production settings validator.
"""
import hashlib
import hmac
import json
import secrets
from dataclasses import dataclass
from urllib.parse import quote

from app.api.core.config import settings


class PaymentProviderError(Exception):
    """The provider refused or failed a checkout call; nothing was confirmed."""


class WebhookRejected(Exception):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class ProviderCheckout:
    provider_ref: str
    checkout_url: str


class SandboxProvider:
    name = "sandbox"

    def __init__(self, merchant: str, secret: str, frontend_url: str):
        self.merchant = merchant
        self._secret = secret
        self._frontend_url = frontend_url.rstrip("/")

    def create_checkout(
        self,
        *,
        public_ref: str,
        public_token: str,
        amount: str,
        currency: str,
        checkout_path: str = "/payment/sandbox",
    ) -> ProviderCheckout:
        # no network call: the reference and the signed return URL are the whole sandbox
        provider_ref = f"sandbox_{secrets.token_urlsafe(18)}"
        query = f"ref={quote(public_ref)}&token={quote(public_token)}"
        return ProviderCheckout(
            provider_ref=provider_ref,
            checkout_url=f"{self._frontend_url}{checkout_path}#{query}",
        )

    def refund(self, attempt, amount) -> str:
        # the sandbox moves no money; the ledger row is the proof of the refund
        return f"sandbox_rf_{secrets.token_urlsafe(18)}"

    def sign(self, raw: bytes) -> str:
        return hmac.new(self._secret.encode("utf-8"), raw, hashlib.sha256).hexdigest()

    def build_event(
        self,
        *,
        event_id: str,
        event_type: str,
        payment_ref: str,
        amount: str,
        currency: str,
    ) -> tuple[bytes, str]:
        body = {
            "id": event_id,
            "type": event_type,
            "provider": self.name,
            "merchant": self.merchant,
            "payment_ref": payment_ref,
            "amount": amount,
            "currency": currency,
        }
        raw = json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return raw, self.sign(raw)

    def parse_webhook(
        self, raw: bytes, signature: str | None, merchant_header: str | None
    ) -> dict:
        if merchant_header != self.merchant:
            raise WebhookRejected("merchant_mismatch")
        if not signature:
            raise WebhookRejected("signature_missing")
        expected = self.sign(raw)
        if not hmac.compare_digest(expected, signature.strip().lower()):
            raise WebhookRejected("signature_invalid")
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise WebhookRejected("payload_invalid")
        if not isinstance(payload, dict):
            raise WebhookRejected("payload_invalid")
        if payload.get("provider") != self.name:
            raise WebhookRejected("provider_mismatch")
        if payload.get("merchant") != self.merchant:
            raise WebhookRejected("merchant_mismatch")
        for key in ("id", "type", "payment_ref", "amount", "currency"):
            value = payload.get(key)
            if not isinstance(value, str) or not value.strip():
                raise WebhookRejected("payload_invalid")
        return payload


def get_service_payment_provider() -> SandboxProvider | None:
    """A configured, non-production sandbox adapter, else deposits stay off."""
    if settings.service_payment_provider.strip().lower() != "sandbox":
        return None
    if settings.environment == "production":
        return None
    secret = settings.service_payment_webhook_secret
    if len(secret) < 16:
        return None
    return SandboxProvider(
        merchant=settings.service_payment_merchant,
        secret=secret,
        frontend_url=settings.frontend_url,
    )
