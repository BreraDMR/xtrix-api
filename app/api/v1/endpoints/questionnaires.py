"""Owner-managed questionnaire drafts.

Everything here is scoped to the caller's own business; a template of another salon
answers the same 404 as a template that never existed.
"""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import TypeAdapter, ValidationError
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.api import deps
from app.api.core.i18n import _
from app.api.core.permissions import get_staff_record, get_user_business_id
from app.db.models.questionnaire import QuestionnaireTemplate, QuestionnaireVersion
from app.db.models.service import Service
from app.db.models.user import User
from app.schemas.questionnaire import (
    QuestionnaireFieldList,
    QuestionnairePublish,
    QuestionnaireTemplateCreate,
    QuestionnaireTemplateOut,
    QuestionnaireTemplateUpdate,
    QuestionnaireVersionOut,
)

router = APIRouter()
_FIELDS = TypeAdapter(QuestionnaireFieldList)


def _owned_business_id(db: Session, user: User, locale: str) -> int:
    """The business whose questionnaires this user may edit.

    A template is owner-level configuration, so staff can't change it even when they
    may manage services; a solo specialist runs their own business and can.
    """
    allowed = user.role == "admin" or (
        user.role in ("business_owner", "specialist") and not get_staff_record(db, user)
    )
    business_id = get_user_business_id(user, db) if allowed else None
    if not allowed:
        raise HTTPException(status_code=403, detail=_("not_allowed", locale))
    if not business_id:
        raise HTTPException(status_code=404, detail=_("business_not_found", locale))
    return business_id


def _latest_versions(db: Session, template_ids: list[int]) -> dict[int, int]:
    if not template_ids:
        return {}
    rows = (
        db.query(QuestionnaireVersion.template_id, func.max(QuestionnaireVersion.version))
        .filter(QuestionnaireVersion.template_id.in_(template_ids))
        .group_by(QuestionnaireVersion.template_id)
        .all()
    )
    return dict(rows)


def _template_out(template: QuestionnaireTemplate, published_version: int | None) -> QuestionnaireTemplateOut:
    return QuestionnaireTemplateOut(
        id=template.id,
        name=template.name,
        description=template.description,
        fields=template.draft_fields,
        service_id=template.service_id,
        published_version=published_version,
        updated_at=template.updated_at,
    )


def _bind_service(
    db: Session,
    template: QuestionnaireTemplate,
    service_id: Optional[int],
    business_id: int,
    locale: str,
) -> None:
    if service_id is None:
        template.service_id = None
        return
    service = (
        db.query(Service.id)
        .filter(
            Service.id == service_id,
            Service.business_id == business_id,
            Service.deleted_at.is_(None),
        )
        .first()
    )
    if not service:
        raise HTTPException(status_code=404, detail=_("service_not_found", locale))
    taken = (
        db.query(QuestionnaireTemplate.id)
        .filter(
            QuestionnaireTemplate.business_id == business_id,
            QuestionnaireTemplate.service_id == service_id,
            QuestionnaireTemplate.id != template.id,
        )
        .first()
    )
    if taken:
        raise HTTPException(status_code=409, detail=_("questionnaire_service_taken", locale))
    template.service_id = service_id


def _own_template(db: Session, template_id: int, business_id: int, locale: str) -> QuestionnaireTemplate:
    template = (
        db.query(QuestionnaireTemplate)
        .filter(
            QuestionnaireTemplate.id == template_id,
            QuestionnaireTemplate.business_id == business_id,
        )
        .first()
    )
    if not template:
        raise HTTPException(status_code=404, detail=_("questionnaire_not_found", locale))
    return template


@router.get("/", response_model=list[QuestionnaireTemplateOut], summary="List questionnaire drafts")
def list_questionnaires(
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
    locale: str = Depends(deps.get_locale),
):
    business_id = _owned_business_id(db, current_user, locale)
    templates = (
        db.query(QuestionnaireTemplate)
        .filter(QuestionnaireTemplate.business_id == business_id)
        .order_by(QuestionnaireTemplate.name, QuestionnaireTemplate.id)
        .all()
    )
    versions = _latest_versions(db, [template.id for template in templates])
    return [_template_out(template, versions.get(template.id)) for template in templates]


@router.post("/", response_model=QuestionnaireTemplateOut, summary="Create a questionnaire draft")
def create_questionnaire(
    payload: QuestionnaireTemplateCreate,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
    locale: str = Depends(deps.get_locale),
):
    business_id = _owned_business_id(db, current_user, locale)
    template = QuestionnaireTemplate(
        business_id=business_id,
        name=payload.name,
        description=payload.description,
        draft_fields=[field.model_dump() for field in payload.fields],
    )
    db.add(template)
    db.commit()
    db.refresh(template)
    return _template_out(template, None)


@router.get("/{template_id}", response_model=QuestionnaireTemplateOut, summary="Get a questionnaire draft")
def get_questionnaire(
    template_id: int,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
    locale: str = Depends(deps.get_locale),
):
    business_id = _owned_business_id(db, current_user, locale)
    template = _own_template(db, template_id, business_id, locale)
    versions = _latest_versions(db, [template.id])
    return _template_out(template, versions.get(template.id))


@router.patch("/{template_id}", response_model=QuestionnaireTemplateOut, summary="Edit a questionnaire draft")
def update_questionnaire(
    template_id: int,
    payload: QuestionnaireTemplateUpdate,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
    locale: str = Depends(deps.get_locale),
):
    business_id = _owned_business_id(db, current_user, locale)
    template = _own_template(db, template_id, business_id, locale)
    changes = payload.model_dump(exclude_unset=True)
    if "name" in changes:
        template.name = changes["name"]
    if "description" in changes:
        template.description = changes["description"]
    if "fields" in changes:
        template.draft_fields = changes["fields"]
    db.commit()
    db.refresh(template)
    versions = _latest_versions(db, [template.id])
    return _template_out(template, versions.get(template.id))


@router.post(
    "/{template_id}/publish",
    response_model=QuestionnaireVersionOut,
    summary="Publish a questionnaire version and bind it to a service",
)
def publish_questionnaire(
    template_id: int,
    payload: Optional[QuestionnairePublish] = None,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
    locale: str = Depends(deps.get_locale),
):
    business_id = _owned_business_id(db, current_user, locale)
    template = _own_template(db, template_id, business_id, locale)
    try:
        fields = _FIELDS.validate_python(template.draft_fields)
    except ValidationError:
        raise HTTPException(status_code=422, detail=_("questionnaire_invalid", locale))
    if not fields:
        raise HTTPException(status_code=422, detail=_("questionnaire_empty", locale))
    if payload is not None and "service_id" in payload.model_fields_set:
        _bind_service(db, template, payload.service_id, business_id, locale)

    latest = (
        db.query(func.max(QuestionnaireVersion.version))
        .filter(QuestionnaireVersion.template_id == template.id)
        .scalar()
    )
    version = QuestionnaireVersion(
        template_id=template.id,
        version=(latest or 0) + 1,
        name=template.name,
        description=template.description,
        fields=[field.model_dump() for field in fields],
        published_by_id=current_user.id,
    )
    db.add(version)
    db.commit()
    db.refresh(version)
    return QuestionnaireVersionOut(
        id=version.id,
        template_id=version.template_id,
        version=version.version,
        name=version.name,
        description=version.description,
        fields=version.fields,
        service_id=template.service_id,
        published_at=version.published_at,
    )
