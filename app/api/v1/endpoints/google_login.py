from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.api import deps
from app.api.core.config import settings
from app.api.middleware.rate_limit import LOGIN_LIMIT, limiter
from app.schemas.google_identity import GoogleConfigOut, GoogleLoginIn, GoogleNonceOut
from app.schemas.user import LoginResponse

router = APIRouter()


@router.get("/config", response_model=GoogleConfigOut)
def google_config():
    client_id = settings.google_client_id.strip()
    return GoogleConfigOut(enabled=bool(client_id), client_id=client_id or None)


@router.post("/nonce", response_model=GoogleNonceOut)
@limiter.limit(LOGIN_LIMIT)
def google_nonce(request: Request, db: Session = Depends(deps.get_db)):
    from app.api.core.google_identity import issue_nonce

    return issue_nonce(db, request)


@router.post("/login", response_model=LoginResponse)
@limiter.limit(LOGIN_LIMIT)
def google_login(request: Request, body: GoogleLoginIn, db: Session = Depends(deps.get_db)):
    from app.api.core.google_identity import login

    return login(db, request, body)
