"""Optional Gmail sender. Credentials are send-only and delivery needs explicit opt-in."""
import base64
import threading
from email.message import EmailMessage

import requests
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials

from app.api.core.config import settings
from app.api.core.notification_transport import DeliveryResult, OutboundMessage

SCOPE = 'https://www.googleapis.com/auth/gmail.send'
SEND_URL = 'https://gmail.googleapis.com/gmail/v1/users/me/messages/send'


class GmailEmailTransport:
    name = 'gmail'

    def __init__(self):
        self._credentials = None
        self._credential_key = None
        self._refresh_lock = threading.Lock()

    def send(self, message: OutboundMessage) -> DeliveryResult:
        if message.channel != 'email':
            return DeliveryResult('skipped', self.name, error='unsupported_channel')
        if not settings.gmail_delivery_enabled:
            return DeliveryResult('skipped', self.name, error='gmail_delivery_disabled')
        allowed = {email.strip().casefold() for email in settings.gmail_allowed_recipients.split(',') if email.strip()}
        if '*' not in allowed and message.recipient.strip().casefold() not in allowed:
            return DeliveryResult('skipped', self.name, error='gmail_recipient_not_allowed')
        if settings.email_provider != "gmail" or not settings.email_provider_configured:
            return DeliveryResult('skipped', self.name, error='gmail_not_configured')
        try:
            mime = EmailMessage()
            mime['Subject'] = message.subject
            mime['From'] = settings.gmail_from
            mime['To'] = message.recipient
            mime.set_content(message.text)
            if message.html:
                mime.add_alternative(message.html, subtype='html')
            raw = base64.urlsafe_b64encode(mime.as_bytes()).decode('ascii')
        except (TypeError, ValueError):
            return DeliveryResult('failed', self.name, error='gmail_invalid_message')
        try:
            key = (settings.gmail_oauth_client_id, settings.gmail_oauth_client_secret.get_secret_value(),
                   settings.gmail_refresh_token.get_secret_value())
            with self._refresh_lock:
                if self._credentials is None or key != self._credential_key:
                    self._credentials = Credentials(None, refresh_token=key[2], client_id=key[0],
                        client_secret=key[1], token_uri='https://oauth2.googleapis.com/token', scopes=[SCOPE])
                    self._credential_key = key
                if not self._credentials.valid:
                    self._credentials.refresh(Request())
                token = self._credentials.token
        except Exception:
            return DeliveryResult('failed', self.name, error='gmail_auth_failed')
        try:
            # Never retry this POST here: a timeout may follow a successful acceptance.
            response = requests.post(SEND_URL, headers={'Authorization': f'Bearer {token}'},
                                     json={'raw': raw}, timeout=15)
        except requests.RequestException:
            return DeliveryResult('failed', self.name, error='gmail_delivery_uncertain')
        if response.status_code >= 500:
            return DeliveryResult('failed', self.name, error='gmail_delivery_uncertain')
        if response.status_code != 200:
            return DeliveryResult('failed', self.name, error=f'gmail_http:{response.status_code}')
        try:
            message_id = response.json().get('id')
        except (ValueError, AttributeError):
            message_id = None
        if not isinstance(message_id, str) or not message_id.strip():
            return DeliveryResult('failed', self.name, error='gmail_delivery_uncertain')
        return DeliveryResult('sent', self.name, provider_message_id=message_id)
