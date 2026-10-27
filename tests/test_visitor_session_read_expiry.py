"""The fixed session deadline still applies when a real SELECT finishes late."""
from datetime import datetime, timedelta

import pytest
from sqlalchemy import event

from app.api.core import visitor_sessions
from app.db.models.visitor_account import VisitorAccount
from app.db.models.visitor_auth import VisitorSession


@pytest.mark.parametrize("entrypoint", ["core", "profile_http"])
def test_session_expiring_during_its_select_is_rejected_without_renewal(db, client, monkeypatch, entrypoint):
    clock = [datetime(2026, 10, 27, 4)]
    monkeypatch.setattr(visitor_sessions, "utcnow", lambda: clock[0])
    owner = VisitorAccount(email_canonical="late-session@example.com", email_verified_at=clock[0])
    db.add(owner)
    db.flush()
    row, raw = visitor_sessions.create_session(db, owner)
    row_id, deadline = row.id, row.expires_at
    db.commit()
    clock[0] = deadline - timedelta(microseconds=1)
    observed = []

    def finish_select(_conn, _cursor, statement, *_args):
        if statement.lstrip().startswith("SELECT") and "FROM visitor_sessions JOIN visitor_accounts" in statement:
            observed.append(statement)
            clock[0] = deadline

    engine = db.get_bind()
    event.listen(engine, "after_cursor_execute", finish_select)
    try:
        if entrypoint == "core":
            result = visitor_sessions.get_valid_session(db, raw)
            assert result is None
        else:
            response = client.get("/visitor/me", headers={"X-Visitor-Token": raw})
            assert response.status_code == 401, response.text
            assert response.headers["cache-control"] == "no-store"
            assert owner.email_canonical not in response.text and raw not in response.text
    finally:
        event.remove(engine, "after_cursor_execute", finish_select)
    assert len(observed) == 1
    db.expire_all()
    stored = db.get(VisitorSession, row_id)
    assert stored.expires_at == deadline and stored.revoked_at is None
