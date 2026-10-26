"""Visitor-only password storage; CRM bcrypt hashes are deliberately unsupported."""
import hashlib
import hmac
import re
import secrets
from threading import BoundedSemaphore

_PREFIX = "$xtrix-visitor-scrypt$v1$32768$8$3$"
_FORMAT = re.compile(re.escape(_PREFIX) + r"([0-9a-f]{32})\$([0-9a-f]{64})\Z")
_ENCODED_LENGTH = len(_PREFIX) + 32 + 1 + 64
_KDF_SLOTS = BoundedSemaphore(2)
_DUMMY_SALT = bytes.fromhex("c27507676165990a0d290629e46006d9")
_DUMMY_DIGEST = bytes(32)
_UNAVAILABLE = "Visitor password hashing is unavailable"


def _password_bytes(password: str) -> bytes | None:
    if not isinstance(password, str) or not 1 <= len(password) <= 128:
        return None
    try:
        return password.encode("utf-8")
    except UnicodeEncodeError:
        return None


def _derive(password: bytes, salt: bytes) -> bytes:
    crypto = getattr(hashlib, "scrypt", None)
    if not callable(crypto):
        raise RuntimeError(_UNAVAILABLE)
    try:
        with _KDF_SLOTS:
            derived = crypto(password, salt=salt, n=32768, r=8, p=3,
                             maxmem=64 * 1024 * 1024, dklen=32)
    except Exception:
        # Never turn an unavailable KDF into a fast hash or expose its input in errors.
        raise RuntimeError(_UNAVAILABLE) from None
    if not isinstance(derived, bytes) or len(derived) != 32:
        raise RuntimeError(_UNAVAILABLE)
    return derived


def _burn_dummy(password: bytes) -> None:
    # One KDF at the real cost. This comparison can never authenticate an account.
    hmac.compare_digest(_derive(password, _DUMMY_SALT), _DUMMY_DIGEST)


def hash_password(password: str) -> str:
    """Hash the complete UTF-8 secret; creation policy is the caller's responsibility."""
    raw = _password_bytes(password)
    if raw is None:
        raise ValueError("Visitor password must contain 1 to 128 valid Unicode characters")
    salt = secrets.token_bytes(16)
    derived = _derive(raw, salt)
    return _PREFIX + salt.hex() + "$" + derived.hex()


def verify_password(password: str, encoded: str | None) -> bool:
    """Verify only our fixed v1 format; missing or invalid hashes burn one dummy KDF.

    Invalid password input is rejected before any expensive work. Runtime failures
    stay RuntimeError so a caller cannot mistake an unavailable KDF for a login.
    """
    raw = _password_bytes(password)
    if raw is None:
        return False
    parsed = (_FORMAT.fullmatch(encoded) if isinstance(encoded, str)
              and len(encoded) == _ENCODED_LENGTH else None)
    if parsed is None:
        _burn_dummy(raw)
        return False
    salt, expected = (bytes.fromhex(value) for value in parsed.groups())
    return hmac.compare_digest(_derive(raw, salt), expected)
