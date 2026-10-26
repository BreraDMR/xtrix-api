"""Read the visitor's account and revoke sessions without entering CRM auth."""
from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from fastapi.routing import APIRoute
from sqlalchemy.orm import Session
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api import deps, visitor_deps
from app.api.core import visitor_sessions
from app.api.core.i18n import _
from app.db.models.visitor_account import VisitorAccount, VisitorGoogleIdentity
from app.db.models.visitor_auth import VisitorSession
from app.schemas.visitor_account import VisitorAccountOut


class _NoStoreRoute(APIRoute):
    def get_route_handler(self):
        original = super().get_route_handler()

        async def private_response(request: Request) -> Response:
            try:
                response = await original(request)
            except StarletteHTTPException as exc:
                # Dependency failures use the same no-store policy as successful reads.
                exc.headers = {**(exc.headers or {}), "Cache-Control": "no-store"}
                raise
            response.headers["Cache-Control"] = "no-store"
            return response

        return private_response


router = APIRouter(route_class=_NoStoreRoute)


@router.get("/me", response_model=VisitorAccountOut)
def read_account(
    session: VisitorSession = Depends(visitor_deps.get_visitor_session),
    db: Session = Depends(deps.get_db),
    locale: str = Depends(deps.get_locale),
) -> VisitorAccountOut:
    account = (db.query(VisitorAccount).filter(VisitorAccount.id == session.account_id)
               .populate_existing().one_or_none())
    if account is None:
        raise HTTPException(status_code=401, detail=_("visitor_session_invalid", locale))
    connected = (db.query(VisitorGoogleIdentity.account_id)
                 .filter(VisitorGoogleIdentity.account_id == account.id).first()) is not None
    return VisitorAccountOut(
        id=account.id,
        email=account.email_canonical,
        email_verified_at=account.email_verified_at,
        created_at=account.created_at,
        has_password=account.hashed_password is not None,
        google_connected=connected,
    )


def _logout(db: Session, token: str | None, *, all_sessions: bool) -> Response:
    # An already invalid credential is a completed logout, not an authentication error.
    first = visitor_sessions.get_valid_session(db, token)
    if first is None:
        return Response(status_code=204)
    expected_account_id, expected_session_id = first.account_id, first.id
    try:
        visitor_sessions.lock_verified_account(db, expected_account_id)
    except visitor_sessions.VisitorAccountUnavailable:
        return Response(status_code=204)
    fresh = visitor_sessions.get_valid_session(db, token)
    if (fresh is None or fresh.account_id != expected_account_id
            or fresh.id != expected_session_id):
        return Response(status_code=204)
    if all_sessions:
        visitor_sessions.revoke_all_sessions(db, account_id=expected_account_id)
    else:
        visitor_sessions.revoke_session(db, account_id=expected_account_id,
                                        session_id=expected_session_id)
    db.commit()
    return Response(status_code=204)


@router.post("/auth/logout", status_code=204, response_class=Response)
def logout(
    x_visitor_token: str | None = Header(default=None, alias="X-Visitor-Token"),
    db: Session = Depends(deps.get_db),
) -> Response:
    return _logout(db, x_visitor_token, all_sessions=False)


@router.post("/auth/logout-all", status_code=204, response_class=Response)
def logout_all(
    x_visitor_token: str | None = Header(default=None, alias="X-Visitor-Token"),
    db: Session = Depends(deps.get_db),
) -> Response:
    return _logout(db, x_visitor_token, all_sessions=True)
