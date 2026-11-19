"""Transport boundary for messages sent to a business's clients.

Every live provider returns what actually happened: sent, skipped or failed. The capture
transport stores messages in memory and reports ``captured``, which is not a delivery - it is
only selected explicitly through NOTIFICATION_TRANSPORT=capture, so a missing provider in
production can never look like a successful send.
"""
import json
import logging
import smtplib
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from email.message import EmailMessage
from typing import Literal, Protocol, runtime_checkable

import requests

from app.api.core.config import settings
from app.api.core.logger import mask_email, mask_phone

logger = logging.getLogger(__name__)

Channel = Literal["email", "sms", "whatsapp", "push"]
EXPO_PUSH_URL = "https://exp.host/--/api/v2/push/send"
Status = Literal["sent", "captured", "skipped", "failed"]


@dataclass(frozen=True)
class OutboundMessage:
    channel: Channel
    recipient: str
    text: str
    subject: str = ""
    html: str | None = None
    # extra fields for the app on the phone (push only)
    data: dict | None = None


@dataclass(frozen=True)
class DeliveryResult:
    status: Status
    provider: str
    provider_message_id: str | None = None
    error: str | None = None

    @property
    def delivered(self) -> bool:
        # a captured message never reached the client, even though the transport accepted it
        return self.status == "sent"


@runtime_checkable
class NotificationTransport(Protocol):
    name: str

    def send(self, message: OutboundMessage) -> DeliveryResult: ...


class SmtpEmailTransport:
    name = "smtp"

    def send(self, message: OutboundMessage) -> DeliveryResult:
        if message.channel != "email":
            return DeliveryResult("skipped", self.name, error="unsupported_channel")
        if not settings.smtp_host or not settings.smtp_from:
            logger.warning("SMTP not configured, skip email to %s", mask_email(message.recipient))
            return DeliveryResult("skipped", self.name, error="smtp_not_configured")
        msg = EmailMessage()
        msg["Subject"] = message.subject
        msg["From"] = settings.smtp_from
        msg["To"] = message.recipient
        msg.set_content(message.text)
        if message.html:
            msg.add_alternative(message.html, subtype="html")
        try:
            with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=15) as server:
                server.starttls()
                if settings.smtp_user and settings.smtp_password:
                    server.login(settings.smtp_user, settings.smtp_password)
                refused = server.send_message(msg)
            if refused:
                logger.error(
                    "SMTP refused %s recipient(s) for %s", len(refused), mask_email(message.recipient)
                )
                return DeliveryResult("failed", self.name, error="smtp_recipient_refused")
            return DeliveryResult("sent", self.name)
        except Exception as exc:
            logger.error("Email send failed via SMTP for %s: %s", mask_email(message.recipient), exc)
            return DeliveryResult("failed", self.name, error=f"smtp_error:{type(exc).__name__}")


class TwilioTransport:
    name = "twilio"

    def send(self, message: OutboundMessage) -> DeliveryResult:
        if message.channel not in ("sms", "whatsapp"):
            return DeliveryResult("skipped", self.name, error="unsupported_channel")
        sender = settings.twilio_whatsapp_from if message.channel == "whatsapp" else settings.twilio_from
        if not settings.twilio_sid or not settings.twilio_token or not sender:
            logger.warning(
                "Twilio %s not configured, skip message to %s",
                message.channel,
                mask_phone(message.recipient),
            )
            return DeliveryResult("skipped", self.name, error="twilio_not_configured")
        to = message.recipient
        if message.channel == "whatsapp":
            if not to.startswith("whatsapp:"):
                if not to.startswith("+"):
                    to = f"+{to}"
                to = f"whatsapp:{to}"
        url = f"https://api.twilio.com/2010-04-01/Accounts/{settings.twilio_sid}/Messages.json"
        try:
            resp = requests.post(
                url,
                data={"From": sender, "To": to, "Body": message.text},
                auth=(settings.twilio_sid, settings.twilio_token),
                timeout=10,
            )
            if 200 <= resp.status_code < 300:
                try:
                    provider_id = resp.json().get("sid")
                except ValueError:
                    provider_id = None
                return DeliveryResult("sent", self.name, provider_message_id=provider_id)
            logger.error("Twilio %s send failed: %s %s", message.channel, resp.status_code, resp.text)
            return DeliveryResult("failed", self.name, error=f"twilio_http:{resp.status_code}")
        except Exception as exc:
            logger.error(
                "Twilio %s send exception for %s: %s", message.channel, mask_phone(to), exc
            )
            return DeliveryResult("failed", self.name, error=f"twilio_error:{type(exc).__name__}")


class ExpoPushTransport:
    """One message per call: a ticket tells whether Expo took it, not whether the phone showed it."""

    name = "expo"

    def send(self, message: OutboundMessage) -> DeliveryResult:
        if message.channel != "push":
            return DeliveryResult("skipped", self.name, error="unsupported_channel")
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        access_token = settings.expo_push_access_token.get_secret_value()
        if access_token:
            headers["Authorization"] = f"Bearer {access_token}"
        body = {"to": message.recipient, "title": message.subject, "body": message.text, "sound": "default"}
        if message.data:
            body["data"] = message.data
        try:
            resp = requests.post(EXPO_PUSH_URL, json=[body], headers=headers, timeout=10)
        except Exception as exc:
            logger.error("Expo push send exception: %s", exc)
            return DeliveryResult("failed", self.name, error=f"expo_error:{type(exc).__name__}")
        if not 200 <= resp.status_code < 300:
            logger.error("Expo push send failed: %s %s", resp.status_code, resp.text[:300])
            return DeliveryResult("failed", self.name, error=f"expo_http:{resp.status_code}")
        try:
            ticket = (resp.json().get("data") or [{}])[0]
        except (ValueError, AttributeError, IndexError):
            return DeliveryResult("failed", self.name, error="expo_bad_response")
        if ticket.get("status") == "ok":
            return DeliveryResult("sent", self.name, provider_message_id=ticket.get("id"))
        code = str((ticket.get("details") or {}).get("error") or "unknown")
        if code == "DeviceNotRegistered":
            # the app was removed from the phone; retrying would never work
            return DeliveryResult("skipped", self.name, error="push_device_not_registered")
        logger.error("Expo push ticket error: %s", code)
        return DeliveryResult("failed", self.name, error=f"expo:{code}"[:500])


class LiveNotificationTransport:
    name = "live"

    def __init__(self) -> None:
        self._smtp = SmtpEmailTransport()
        from app.api.core.gmail_transport import GmailEmailTransport
        self._gmail = GmailEmailTransport()
        self._twilio = TwilioTransport()
        self._expo = ExpoPushTransport()

    def send(self, message: OutboundMessage) -> DeliveryResult:
        if settings.pilot_mode_enabled and message.channel != "email":
            return DeliveryResult("skipped", self.name, error="pilot_delivery_disabled")
        if message.channel == "email":
            return (self._gmail if settings.email_provider == "gmail" else self._smtp).send(message)
        if message.channel in ("sms", "whatsapp"):
            return self._twilio.send(message)
        if message.channel == "push":
            return self._expo.send(message)
        return DeliveryResult("skipped", self.name, error="unsupported_channel")


class CaptureNotificationTransport:
    """In-memory transport for local checks; it never opens a network connection."""

    name = "capture"

    def __init__(self) -> None:
        self._messages: list[OutboundMessage] = []
        self._lock = threading.Lock()

    def send(self, message: OutboundMessage) -> DeliveryResult:
        with self._lock:
            self._messages.append(message)
            self._write_file(message)
        if message.channel == "email":
            masked = mask_email(message.recipient)
        elif message.channel == "push":
            masked = f"{message.recipient[:18]}***"
        else:
            masked = mask_phone(message.recipient)
        logger.info(
            "Notification captured by capture transport for %s (%s); nothing was sent",
            masked,
            message.channel,
        )
        return DeliveryResult("captured", self.name)

    def _write_file(self, message: OutboundMessage) -> None:
        path = (settings.capture_mail_file or "").strip()
        if not path:
            return
        record = {
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "channel": message.channel,
            "recipient": message.recipient,
            "subject": message.subject,
            "text": message.text,
            "html": message.html,
            "data": message.data,
        }
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    def messages(self) -> list[OutboundMessage]:
        with self._lock:
            return list(self._messages)

    def clear(self) -> None:
        with self._lock:
            self._messages.clear()


_transport: NotificationTransport | None = None
_transport_lock = threading.Lock()


def _build_transport() -> NotificationTransport:
    mode = (settings.notification_transport or "").strip().lower()
    if mode == "capture":
        logger.warning(
            "Notification transport is set to capture: messages are kept in memory and are NOT delivered"
        )
        return CaptureNotificationTransport()
    if mode not in ("", "live"):
        logger.warning("Unknown notification transport %r, falling back to live providers", mode)
    return LiveNotificationTransport()


class DisabledNotificationTransport:
    name = "disabled"

    def send(self, message: OutboundMessage) -> DeliveryResult:
        return DeliveryResult("skipped", self.name, error="pilot_delivery_disabled")


def get_notification_transport() -> NotificationTransport:
    if settings.pilot_mode_enabled and not settings.pilot_email_delivery_enabled:
        return DisabledNotificationTransport()
    global _transport
    if _transport is None:
        with _transport_lock:
            if _transport is None:
                _transport = _build_transport()
    return _transport
