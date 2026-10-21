"""Guest answers by token, plus the protected view used by the client card.

The public routes take the raw token from a header, never from the path, so access
logs don't keep a usable link. The token itself decides the visit and the version.
"""
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api import deps
from app.api.core import audit_service, questionnaire_access
from app.api.core.i18n import _
from app.api.core.permissions import check_visit_access
from app.api.core.questionnaire_answers import AnswersInvalid, answers_hash, normalize_answers
from app.api.middleware.rate_limit import (
    PUBLIC_QUESTIONNAIRE_READ_LIMIT,
    PUBLIC_QUESTIONNAIRE_SUBMIT_LIMIT,
    limiter,
)
from app.db.models.questionnaire import QuestionnaireVersion
from app.db.models.questionnaire_response import QuestionnaireResponse
from app.db.models.user import User
from app.db.models.visit import Visit
from app.schemas.questionnaire import (
    QuestionnaireAnswersIn,
    QuestionnaireAnswersOut,
    QuestionnaireFormOut,
    QuestionnaireSubmittedOut,
)

router = APIRouter()
staff_router = APIRouter()


def _resolve(raw_token: Optional[str], db: Session, locale: str):
    try:
        return questionnaire_access.resolve_token(db, raw_token)
    except questionnaire_access.QuestionnaireTokenInvalid:
        raise HTTPException(
            status_code=404, detail=_("questionnaire_link_unavailable", locale)
        )


def _by_visit(db: Session, visit_id: int) -> Optional[QuestionnaireResponse]:
    return (
        db.query(QuestionnaireResponse)
        .filter(QuestionnaireResponse.visit_id == visit_id)
        .first()
    )


@router.get("", response_model=QuestionnaireFormOut, summary="Load a questionnaire by its guest token")
@limiter.limit(PUBLIC_QUESTIONNAIRE_READ_LIMIT)
def get_questionnaire(
    request: Request,
    x_questionnaire_token: Optional[str] = Header(None, alias="X-Questionnaire-Token"),
    db: Session = Depends(deps.get_db),
    locale: str = Depends(deps.get_locale),
):
    token, version, visit = _resolve(x_questionnaire_token, db, locale)
    response = _by_visit(db, visit.id)
    return QuestionnaireFormOut(
        version=version.version,
        name=version.name,
        description=version.description,
        fields=version.fields,
        expires_at=token.expires_at,
        submitted_at=response.created_at if response else None,
    )


@router.post("/answers", response_model=QuestionnaireSubmittedOut, summary="Submit answers with a guest token")
@limiter.limit(PUBLIC_QUESTIONNAIRE_SUBMIT_LIMIT)
def submit_questionnaire(
    request: Request,
    payload: QuestionnaireAnswersIn,
    x_questionnaire_token: Optional[str] = Header(None, alias="X-Questionnaire-Token"),
    db: Session = Depends(deps.get_db),
    locale: str = Depends(deps.get_locale),
):
    token, version, visit = _resolve(x_questionnaire_token, db, locale)
    try:
        answers = normalize_answers(version.fields, payload.answers)
    except AnswersInvalid as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "message": _("questionnaire_answers_invalid", locale),
                "fields": exc.fields,
            },
        )
    request_hash = answers_hash(answers)

    existing = _by_visit(db, visit.id)
    if existing:
        if existing.version_id == version.id and existing.request_hash == request_hash:
            return QuestionnaireSubmittedOut(version=version.version, submitted_at=existing.created_at)
        raise HTTPException(status_code=409, detail=_("questionnaire_already_submitted", locale))

    response = QuestionnaireResponse(
        business_id=token.business_id,
        visit_id=visit.id,
        version_id=version.id,
        client_id=visit.client_id,
        access_token_id=token.id,
        answers=answers,
        request_hash=request_hash,
    )
    db.add(response)
    try:
        db.flush()
        audit_service.record_event(
            db,
            action="questionnaire.submitted",
            entity_type="questionnaire",
            entity_id=response.id,
            business_id=token.business_id,
            changes={
                "version": version.version,
                "fields_count": len(version.fields or []),
                "answered_count": len(answers),
            },
            actor_name="guest",
            actor_role="guest",
            request_id=audit_service.request_id_for(request),
        )
        db.commit()
    except IntegrityError:
        db.rollback()
        existing = _by_visit(db, visit.id)
        if existing and existing.version_id == version.id and existing.request_hash == request_hash:
            return QuestionnaireSubmittedOut(version=version.version, submitted_at=existing.created_at)
        raise HTTPException(status_code=409, detail=_("questionnaire_already_submitted", locale))
    db.refresh(response)
    return QuestionnaireSubmittedOut(version=version.version, submitted_at=response.created_at)


@staff_router.get(
    "/{visit_id}/questionnaire",
    response_model=QuestionnaireAnswersOut,
    summary="Read a visit's questionnaire answers",
)
def get_visit_questionnaire(
    visit_id: int,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(
        deps.role_required("admin", "business_owner", "specialist", "employee")
    ),
    locale: str = Depends(deps.get_locale),
):
    visit = (
        db.query(Visit)
        .filter(Visit.id == visit_id, Visit.deleted_at.is_(None))
        .first()
    )
    if not visit:
        raise HTTPException(status_code=404, detail=_("visit_not_found", locale))
    if not check_visit_access(db, current_user, visit):
        raise HTTPException(status_code=403, detail=_("not_allowed", locale))
    response = (
        db.query(QuestionnaireResponse)
        .filter(
            QuestionnaireResponse.visit_id == visit.id,
            QuestionnaireResponse.business_id == visit.business_id,
        )
        .first()
    )
    if not response:
        raise HTTPException(
            status_code=404, detail=_("questionnaire_response_not_found", locale)
        )
    version = (
        db.query(QuestionnaireVersion)
        .filter(QuestionnaireVersion.id == response.version_id)
        .first()
    )
    if not version:
        raise HTTPException(
            status_code=404, detail=_("questionnaire_response_not_found", locale)
        )
    return QuestionnaireAnswersOut(
        version=version.version,
        name=version.name,
        description=version.description,
        fields=version.fields,
        answers=response.answers,
        submitted_at=response.created_at,
    )
