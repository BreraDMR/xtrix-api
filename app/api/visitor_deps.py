"""Visitor credentials never inherit CRM authentication or subscription gates."""
from fastapi import Depends, Header, HTTPException
from sqlalchemy.orm import Session

from app.api.core import visitor_sessions
from app.api.core.i18n import _
from app.api.deps import get_db, get_locale
from app.db.models.visitor_auth import VisitorSession


def _require_session(db: Session, token: str | None, locale: str) -> VisitorSession:
    row = visitor_sessions.get_valid_session(db, token)
    if row is None:
        raise HTTPException(status_code=401, detail=_("visitor_session_invalid", locale))
    return row


def get_visitor_session(
    x_visitor_token: str | None = Header(default=None, alias="X-Visitor-Token"),
    db: Session = Depends(get_db),
    locale: str = Depends(get_locale),
) -> VisitorSession:
    return _require_session(db, x_visitor_token, locale)


def get_locked_visitor_session(
    x_visitor_token: str | None = Header(default=None, alias="X-Visitor-Token"),
    db: Session = Depends(get_db),
    locale: str = Depends(get_locale),
) -> VisitorSession:
    first = _require_session(db, x_visitor_token, locale)
    expected_account_id = first.account_id
    try:
        visitor_sessions.lock_verified_account(db, expected_account_id)
    except ValueError:
        raise HTTPException(status_code=401, detail=_("visitor_session_invalid", locale)) from None
    # Reset/revocation may have committed while the account lock was unavailable.
    fresh = _require_session(db, x_visitor_token, locale)
    if fresh.account_id != expected_account_id:
        raise HTTPException(status_code=401, detail=_("visitor_session_invalid", locale))
    return fresh
