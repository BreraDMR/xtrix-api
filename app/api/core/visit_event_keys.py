"""Keys for distinct committed visit lifecycle transitions."""
from uuid import uuid4


def cancellation_event_key(visit_id: int) -> str:
    # Call only after the locked visit actually changes to cancelled.
    return f"visit:{visit_id}:cancelled:{uuid4().hex}"
