"""Issue and resolve visit-scoped questionnaire tokens.

The raw token is returned once and never stored or logged; the row keeps only its
SHA-256 hash. A token names one visit and one published version, so a guest can't
swap either by editing the link or the request.
"""
import hashlib
import secrets
from datetime import timedelta
from typing import Optional

from sqlalchemy.orm import Session

from app.api.core.clock import utcnow
from app.db.models.questionnaire import QuestionnaireTemplate, QuestionnaireVersion
from app.db.models.questionnaire_access import QuestionnaireAccessToken
from app.db.models.visit import Visit

TOKEN_TTL = timedelta(days=7)
TOKEN_BYTES = 32
TOKEN_MAX_LENGTH = 128


class QuestionnaireTokenInvalid(Exception):
    """Unknown, expired, or no longer attached to a live visit/version."""


def _hash(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def latest_version_for_service(
    db: Session, business_id: int, service_id: Optional[int]
) -> Optional[QuestionnaireVersion]:
    if not service_id:
        return None
    template = (
        db.query(QuestionnaireTemplate)
        .filter(
            QuestionnaireTemplate.business_id == business_id,
            QuestionnaireTemplate.service_id == service_id,
        )
        .first()
    )
    if not template:
        return None
    return (
        db.query(QuestionnaireVersion)
        .filter(QuestionnaireVersion.template_id == template.id)
        .order_by(QuestionnaireVersion.version.desc())
        .first()
    )


def issue_visit_token(
    db: Session,
    visit: Visit,
    created_by_id: Optional[int],
    ttl: timedelta = TOKEN_TTL,
) -> Optional[tuple[str, QuestionnaireAccessToken, QuestionnaireVersion]]:
    version = latest_version_for_service(db, visit.business_id, visit.service_id)
    if not version:
        return None
    raw = secrets.token_urlsafe(TOKEN_BYTES)
    token = QuestionnaireAccessToken(
        business_id=visit.business_id,
        visit_id=visit.id,
        version_id=version.id,
        created_by_id=created_by_id,
        token_hash=_hash(raw),
        expires_at=utcnow() + ttl,
    )
    db.add(token)
    db.commit()
    db.refresh(token)
    return raw, token, version


def resolve_token(
    db: Session, raw_token: Optional[str]
) -> tuple[QuestionnaireAccessToken, QuestionnaireVersion, Visit]:
    if not raw_token or len(raw_token) > TOKEN_MAX_LENGTH:
        raise QuestionnaireTokenInvalid
    token = (
        db.query(QuestionnaireAccessToken)
        .filter(QuestionnaireAccessToken.token_hash == _hash(raw_token))
        .first()
    )
    if not token or token.expires_at <= utcnow():
        raise QuestionnaireTokenInvalid
    visit = (
        db.query(Visit)
        .filter(Visit.id == token.visit_id, Visit.deleted_at.is_(None))
        .first()
    )
    version = (
        db.query(QuestionnaireVersion)
        .filter(QuestionnaireVersion.id == token.version_id)
        .first()
    )
    template = version.template if version else None
    if (
        not visit
        or not version
        or not template
        or visit.business_id != token.business_id
        or template.business_id != token.business_id
    ):
        raise QuestionnaireTokenInvalid
    from app.api.core.subscription_access import enforce_business_access
    enforce_business_access(db, token.business_id)
    return token, version, visit
