"""Transport boundary for messages sent to a business's clients.

Every live provider returns what actually happened: sent, skipped or failed. The capture
transport stores messages in memory and reports ``captured``, which is not a delivery - it is
only selected explicitly through NOTIFICATION_TRANSPORT=capture, so a missing provider in
production can never look like a successful send.
"""
import logging
import smtplib
import threading
from dataclasses import dataclass
from email.message import EmailMessage
from typing import Literal, Protocol, runtime_checkable

import requests

from app.api.core.config import settings
from app.api.core.logger import mask_email, mask_phone

logger = logging.getLogger(__name__)

Channel = Literal["email", "sms", "whatsapp"]
Status = Literal["sent", "captured", "skipped", "failed"]


@dataclass(frozen=True)
class OutboundMessage:
    channel: Channel
    recipient: str
    text: str
    subject: str = ""
    html: str | None = None


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
            with smtplib.SMTP(settings.smtp_host, settings.smtp_port) as server:
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


class LiveNotificationTransport:
    name = "live"

    def __init__(self) -> None:
        self._smtp = SmtpEmailTransport()
        self._twilio = TwilioTransport()

    def send(self, message: OutboundMessage) -> DeliveryResult:
        if message.channel == "email":
            return self._smtp.send(message)
        if message.channel in ("sms", "whatsapp"):
            return self._twilio.send(message)
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
        masked = mask_email(message.recipient) if message.channel == "email" else mask_phone(message.recipient)
        logger.info(
            "Notification captured by capture transport for %s (%s); nothing was sent",
            masked,
            message.channel,
        )
        return DeliveryResult("captured", self.name)

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


def get_notification_transport() -> NotificationTransport:
    global _transport
    if _transport is None:
        with _transport_lock:
            if _transport is None:
                _transport = _build_transport()
    return _transport
