"""The visitor's phone signs up for visit reminders and signs off on logout."""
from fastapi import APIRouter, Depends, Path, Response
from sqlalchemy.orm import Session

from app.api import deps, visitor_deps
from app.api.core import notification_push
from app.api.private_routes import NoStoreRoute
from app.db.models.visitor_auth import VisitorSession
from app.schemas.visitor_push import VisitorPushTokenIn

router = APIRouter(route_class=NoStoreRoute)


@router.put("/push-tokens", status_code=204, response_class=Response,
            summary="Register this phone for visit reminders")
def register_push_token(
    body: VisitorPushTokenIn,
    session: VisitorSession = Depends(visitor_deps.get_visitor_session),
    db: Session = Depends(deps.get_db),
    locale: str = Depends(deps.get_locale),
) -> Response:
    notification_push.register(db, session, body.token, body.platform, locale)
    db.commit()
    return Response(status_code=204)


@router.delete("/push-tokens/{token}", status_code=204, response_class=Response,
               summary="Stop visit reminders on this phone")
def unregister_push_token(
    token: str = Path(..., max_length=255),
    session: VisitorSession = Depends(visitor_deps.get_visitor_session),
    db: Session = Depends(deps.get_db),
) -> Response:
    # a token that is unknown or someone else's is a finished unregister, not an error
    notification_push.unregister(db, session, token)
    db.commit()
    return Response(status_code=204)
