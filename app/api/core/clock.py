from datetime import datetime, timezone


def utcnow() -> datetime:
    """Current UTC time as a naive datetime - that's how every DateTime column stores it.

    Same result as the deprecated datetime.utcnow(), without the warning on Python 3.12+.
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)
