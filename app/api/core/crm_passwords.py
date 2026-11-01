"""Versioned CRM password hashes; the legacy bcrypt verifier stays in security."""
import hmac
import re
import secrets

from app.api.core import visitor_passwords

_PREFIX = "$xtrix-crm-scrypt$v1$32768$8$3$"
_FORMAT = re.compile(re.escape(_PREFIX) + r"([0-9a-f]{32})\$([0-9a-f]{64})\Z")
_ENCODED_LENGTH = len(_PREFIX) + 32 + 1 + 64
_DOMAIN = b"Xtrix CRM password v1\0"
_UNAVAILABLE = "CRM password hashing is unavailable"


def _password_bytes(password: str) -> bytes | None:
    if not isinstance(password, str) or not 1 <= len(password) <= 128:
        return None
    try:
        return password.encode("utf-8")
    except UnicodeEncodeError:
        return None


def _derive(raw: bytes, salt: bytes) -> bytes:
    # Share the bounded KDF slots, but keep CRM and visitor secrets in separate domains.
    try:
        return visitor_passwords._derive(_DOMAIN + raw, salt)
    except RuntimeError:
        raise RuntimeError(_UNAVAILABLE) from None


def hash_password(password: str) -> str:
    raw = _password_bytes(password)
    if raw is None:
        raise ValueError("CRM password must contain 1 to 128 valid Unicode characters")
    salt = secrets.token_bytes(16)
    return _PREFIX + salt.hex() + "$" + _derive(raw, salt).hex()


def verify_password(password: str, encoded: str | None) -> bool:
    raw = _password_bytes(password)
    if raw is None:
        return False
    parsed = (_FORMAT.fullmatch(encoded) if isinstance(encoded, str)
              and len(encoded) == _ENCODED_LENGTH else None)
    if parsed is None:
        return False
    salt, expected = (bytes.fromhex(value) for value in parsed.groups())
    return hmac.compare_digest(_derive(raw, salt), expected)
