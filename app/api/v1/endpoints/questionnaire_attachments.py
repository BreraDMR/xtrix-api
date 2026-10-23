"""Questionnaire file uploads and the permission-checked download links.

The guest gets one presigned POST with a server-made key; staff links are signed only
after the visit access check. A client-supplied storage key is never accepted.
"""
from datetime import timedelta
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlalchemy.orm import Session

from app.api import deps
from app.api.core import questionnaire_access, questionnaire_attachments as attachments
from app.api.core.clock import utcnow
from app.api.core.i18n import _
from app.api.core.permissions import check_visit_access
from app.api.middleware.rate_limit import PUBLIC_QUESTIONNAIRE_SUBMIT_LIMIT, limiter
from app.db.models.questionnaire_attachment import QuestionnaireAttachment
from app.db.models.user import User
from app.db.models.visit import Visit
from app.schemas.questionnaire import (
    QuestionnaireAttachmentCreate,
    QuestionnaireAttachmentOut,
    QuestionnaireAttachmentUploadOut,
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


def _attachment_out(attachment: QuestionnaireAttachment, url: Optional[str] = None):
    return QuestionnaireAttachmentOut(
        id=attachment.id,
        filename=attachment.original_filename,
        content_type=attachment.content_type,
        size_bytes=attachment.size_bytes,
        uploaded_at=attachment.uploaded_at,
        url=url,
    )


@router.post(
    "/attachments",
    response_model=QuestionnaireAttachmentUploadOut,
    summary="Reserve a private upload for a questionnaire",
)
@limiter.limit(PUBLIC_QUESTIONNAIRE_SUBMIT_LIMIT)
def create_attachment(
    request: Request,
    payload: QuestionnaireAttachmentCreate,
    x_questionnaire_token: Optional[str] = Header(None, alias="X-Questionnaire-Token"),
    db: Session = Depends(deps.get_db),
    locale: str = Depends(deps.get_locale),
):
    token, version, visit = _resolve(x_questionnaire_token, db, locale)
    if payload.content_type not in attachments.ALLOWED_CONTENT_TYPES:
        raise HTTPException(
            status_code=422, detail=_("questionnaire_attachment_type", locale)
        )
    # one reservation at a time per visit, otherwise two tabs both see 4 and both insert
    db.query(Visit.id).filter(Visit.id == visit.id).with_for_update().first()
    # a reservation nobody finished after its presigned POST expired is dead weight;
    # drop it so a guest who closed the tab can still upload
    abandoned_before = utcnow() - timedelta(seconds=attachments.UPLOAD_URL_TTL + 60)
    db.query(QuestionnaireAttachment).filter(
        QuestionnaireAttachment.visit_id == visit.id,
        QuestionnaireAttachment.uploaded_at.is_(None),
        QuestionnaireAttachment.created_at < abandoned_before,
    ).delete(synchronize_session=False)
    used = (
        db.query(QuestionnaireAttachment.id)
        .filter(QuestionnaireAttachment.visit_id == visit.id)
        .count()
    )
    if used >= attachments.MAX_ATTACHMENTS_PER_VISIT:
        raise HTTPException(
            status_code=409, detail=_("questionnaire_attachment_limit", locale)
        )

    key = attachments.build_storage_key(token.business_id, visit.id, payload.content_type)
    try:
        presigned = attachments.presign_upload(key, payload.content_type)
    except attachments.UploadsUnavailable:
        raise HTTPException(
            status_code=503, detail=_("questionnaire_uploads_unavailable", locale)
        )

    attachment = QuestionnaireAttachment(
        business_id=token.business_id,
        visit_id=visit.id,
        version_id=version.id,
        token_id=token.id,
        storage_key=key,
        original_filename=attachments.sanitize_filename(payload.filename),
        content_type=payload.content_type,
        size_bytes=payload.size_bytes,
    )
    db.add(attachment)
    db.commit()
    db.refresh(attachment)
    return QuestionnaireAttachmentUploadOut(
        attachment_id=attachment.id,
        upload_url=presigned["url"],
        upload_fields={name: str(value) for name, value in presigned["fields"].items()},
    )


@router.post(
    "/attachments/{attachment_id}/complete",
    response_model=QuestionnaireAttachmentOut,
    summary="Confirm that a reserved upload reached private storage",
)
@limiter.limit(PUBLIC_QUESTIONNAIRE_SUBMIT_LIMIT)
def complete_attachment(
    request: Request,
    attachment_id: int,
    x_questionnaire_token: Optional[str] = Header(None, alias="X-Questionnaire-Token"),
    db: Session = Depends(deps.get_db),
    locale: str = Depends(deps.get_locale),
):
    token, _version, visit = _resolve(x_questionnaire_token, db, locale)
    attachment = (
        db.query(QuestionnaireAttachment)
        .filter(
            QuestionnaireAttachment.id == attachment_id,
            QuestionnaireAttachment.visit_id == visit.id,
            QuestionnaireAttachment.business_id == token.business_id,
        )
        .first()
    )
    if not attachment:
        raise HTTPException(
            status_code=404, detail=_("questionnaire_attachment_not_found", locale)
        )
    if attachment.uploaded_at:
        return _attachment_out(attachment)

    try:
        content_type, size = attachments.head_uploaded(attachment.storage_key)
    except attachments.AttachmentMissing:
        raise HTTPException(
            status_code=409, detail=_("questionnaire_attachment_missing", locale)
        )
    except attachments.UploadsUnavailable:
        raise HTTPException(
            status_code=503, detail=_("questionnaire_uploads_unavailable", locale)
        )
    if (
        content_type != attachment.content_type
        or content_type not in attachments.ALLOWED_CONTENT_TYPES
        or not 0 < size <= attachments.MAX_ATTACHMENT_BYTES
    ):
        raise HTTPException(
            status_code=422, detail=_("questionnaire_attachment_invalid", locale)
        )
    attachment.content_type = content_type
    attachment.size_bytes = size
    attachment.uploaded_at = utcnow()
    db.commit()
    db.refresh(attachment)
    return _attachment_out(attachment)


@staff_router.get(
    "/{visit_id}/questionnaire/attachments",
    response_model=list[QuestionnaireAttachmentOut],
    summary="List a visit's questionnaire files with permission-checked links",
)
def list_visit_attachments(
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
    uploaded = (
        db.query(QuestionnaireAttachment)
        .filter(
            QuestionnaireAttachment.visit_id == visit.id,
            QuestionnaireAttachment.business_id == visit.business_id,
            QuestionnaireAttachment.uploaded_at.isnot(None),
        )
        .order_by(QuestionnaireAttachment.created_at, QuestionnaireAttachment.id)
        .all()
    )
    result = []
    for attachment in uploaded:
        try:
            url = attachments.presign_download(attachment.storage_key)
        except attachments.UploadsUnavailable:
            url = None
        result.append(_attachment_out(attachment, url))
    return result
