"""Visitor portal: a separate, limited surface for a salon's own clients.

Every route is scoped by the opaque session to one business and one client. The
response never contains staff notes, payment methods, another client's visits or
business finance. Sign-in links are single-use and delivered through the outbox.
"""
from app.api.core.loyalty_balance_view import balance_payload

from datetime import timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Query
from sqlalchemy import func, and_, case
from sqlalchemy.orm import Session, joinedload

from app.api import deps
from app.api.private_routes import NoStoreRoute
from app.api.core import booking_manage_access
from app.api.core.client_portal_email import KIND as PORTAL_LOGIN_KIND
from app.api.core.client_portal_email import queue_portal_login
from app.api.core.clock import utcnow
from app.api.core.i18n import _
from app.api.crud import crud_client_portal, crud_public_booking
from app.api.middleware.rate_limit import (
    CLIENT_PORTAL_LOGIN_LIMIT,
    CLIENT_PORTAL_VERIFY_LIMIT,
    limiter,
)
from app.db.models.business import Business
from app.db.models.client import Client
from app.db.models.client_business_relation import ClientBusinessRelation
from app.db.models.client_portal import ClientPortalSession
from app.db.models.loyalty_program import LoyaltyLevel
from app.db.models.notification_outbox import NotificationOutbox
from app.db.models.public_booking import PublicBookingSettings
from app.db.models.questionnaire import QuestionnaireVersion
from app.db.models.questionnaire_response import QuestionnaireResponse
from app.db.models.visit import Visit
from app.schemas.client_portal import (
    ClientPortalLoginIn,
    ClientPortalLoyaltyOut,
    ClientPortalManageLinkOut,
    ClientPortalMessageOut,
    ClientPortalQuestionnaireOut,
    ClientPortalQuestionnairesOut,
    ClientPortalSessionInfoOut,
    ClientPortalSessionOut,
    ClientPortalVerifyIn,
    ClientPortalVisitOut,
    ClientPortalVisitsOut,
)

router = APIRouter(route_class=NoStoreRoute)
REQUEST_COOLDOWN = timedelta(seconds=60)


def _settings_by_slug(db: Session, slug: str) -> Optional[PublicBookingSettings]:
    # the portal is not the public booking funnel: a salon may keep booking off and
    # still let its own clients see their visits, so is_enabled is not required here
    return (
        db.query(PublicBookingSettings)
        .join(Business, Business.id == PublicBookingSettings.business_id)
        .filter(
            PublicBookingSettings.slug == slug,
            Business.deleted_at.is_(None),
        )
        .first()
    )


def _session_context(
    db: Session, session: ClientPortalSession
) -> tuple[Client, Business, Optional[PublicBookingSettings]]:
    client = db.get(Client, session.client_id)
    business = db.get(Business, session.business_id)
    if (
        client is None
        or client.deleted_at is not None
        or business is None
        or business.deleted_at is not None
    ):
        raise HTTPException(status_code=401, detail=_("portal_session_invalid"))
    settings = crud_public_booking.get_settings(db, business.id)
    from app.api.core.subscription_access import enforce_business_access
    enforce_business_access(db, business.id, write=False)
    return client, business, settings


def _session_info(
    client: Client, business: Business, settings: Optional[PublicBookingSettings], expires_at
) -> ClientPortalSessionInfoOut:
    return ClientPortalSessionInfoOut(
        expires_at=expires_at,
        client_name=client.full_name,
        business_name=business.name,
        business_slug=settings.slug if settings else None,
        language=client.language if client.language in ("en", "ru", "cz") else "en",
    )


@router.post(
    "/{slug}/request",
    response_model=ClientPortalMessageOut,
    status_code=202,
    summary="Email a one-time visitor portal sign-in link",
)
@limiter.limit(CLIENT_PORTAL_LOGIN_LIMIT)
def request_portal_link(
    request: Request,
    slug: str,
    payload: ClientPortalLoginIn,
    db: Session = Depends(deps.get_db),
    locale: str = Depends(deps.get_locale),
):
    email = str(payload.email)
    settings = _settings_by_slug(db, slug)
    if settings is not None:
        from app.api.core.subscription_access import enforce_business_access
        enforce_business_access(db, settings.business_id)
        business = db.get(Business, settings.business_id)
        client = crud_public_booking.find_business_client_by_contact(
            db, settings.business_id, None, email
        )
        if business is not None and client is not None and client.contact_email:
            recent = (
                db.query(NotificationOutbox.id)
                .filter(
                    NotificationOutbox.kind == PORTAL_LOGIN_KIND,
                    NotificationOutbox.business_id == business.id,
                    func.lower(NotificationOutbox.recipient) == email.lower(),
                    NotificationOutbox.status.in_(("pending", "processing", "captured", "sent")),
                    NotificationOutbox.created_at > utcnow() - REQUEST_COOLDOWN,
                )
                .first()
            )
            if recent is None:
                queue_portal_login(db, business, client)
                db.commit()
    # the same answer for an unknown salon, an unknown email and a real client
    return ClientPortalMessageOut(message=_("portal_link_requested", locale))


@router.post(
    "/verify",
    response_model=ClientPortalSessionOut,
    summary="Exchange a one-time link for a limited visitor session",
)
@limiter.limit(CLIENT_PORTAL_VERIFY_LIMIT)
def verify_portal_link(
    request: Request,
    payload: ClientPortalVerifyIn,
    db: Session = Depends(deps.get_db),
    locale: str = Depends(deps.get_locale),
):
    token = crud_client_portal.consume_magic_token(db, payload.token)
    if token is None:
        raise HTTPException(status_code=400, detail=_("portal_link_invalid", locale))
    client = db.get(Client, token.client_id)
    business = db.get(Business, token.business_id)
    relation = (
        db.query(ClientBusinessRelation.id)
        .filter(
            ClientBusinessRelation.client_id == token.client_id,
            ClientBusinessRelation.business_id == token.business_id,
        )
        .first()
    )
    if (
        client is None
        or client.deleted_at is not None
        or business is None
        or business.deleted_at is not None
        or relation is None
    ):
        # the link is burned even when the client was removed after it was sent
        db.commit()
        raise HTTPException(status_code=400, detail=_("portal_link_invalid", locale))
    session, raw_token = crud_client_portal.create_session(db, business.id, client.id)
    db.commit()
    db.refresh(session)
    settings = crud_public_booking.get_settings(db, business.id)
    info = _session_info(client, business, settings, session.expires_at)
    return ClientPortalSessionOut(session_token=raw_token, **info.model_dump())


@router.get(
    "/session",
    response_model=ClientPortalSessionInfoOut,
    summary="Read the current visitor session",
)
def read_portal_session(
    session: ClientPortalSession = Depends(deps.get_portal_session),
    db: Session = Depends(deps.get_db),
):
    client, business, settings = _session_context(db, session)
    return _session_info(client, business, settings, session.expires_at)


@router.post("/logout", response_model=ClientPortalMessageOut, summary="Revoke the visitor session")
def logout(
    session: ClientPortalSession = Depends(deps.get_portal_session),
    db: Session = Depends(deps.get_db),
    locale: str = Depends(deps.get_locale),
):
    crud_client_portal.revoke_session(db, session)
    return ClientPortalMessageOut(message=_("portal_session_invalid", locale))


@router.get(
    "/visits",
    response_model=ClientPortalVisitsOut,
    summary="List the client's own upcoming and past visits",
)
def list_portal_visits(
    limit: int = Query(100, ge=1, le=100),
    offset: int = Query(0, ge=0, le=1000000),
    session: ClientPortalSession = Depends(deps.get_portal_session),
    db: Session = Depends(deps.get_db),
):
    client, business, settings = _session_context(db, session)
    now = utcnow()
    is_upcoming = and_(func.coalesce(Visit.status, "planned").notin_(["cancelled", "completed"]),
                       Visit.date_start >= now)
    visits = (
        db.query(Visit)
        .options(joinedload(Visit.specialist))
        .filter(
            Visit.business_id == business.id,
            Visit.client_id == client.id,
            Visit.deleted_at.is_(None),
        )
        .order_by(case((is_upcoming, 0), else_=1), case((is_upcoming, Visit.date_start)),
                  Visit.date_start.desc(), Visit.id)
        .offset(offset)
        .limit(limit + 1)
        .all()
    )
    next_offset = offset + limit if len(visits) > limit else None
    visits = visits[:limit]

    def out(visit: Visit) -> ClientPortalVisitOut:
        status = visit.status or "planned"
        return ClientPortalVisitOut(
            id=visit.id,
            date_start=visit.date_start,
            date_end=visit.date_end,
            service_id=visit.service_id,
            service_name=visit.service_name,
            staff_name=visit.specialist.full_name if visit.specialist else None,
            status=status,
            price=visit.price,
            can_manage=status not in ("cancelled", "completed") and visit.date_start >= now,
        )

    upcoming = sorted(
        (
            visit
            for visit in visits
            if (visit.status or "planned") not in ("cancelled", "completed")
            and visit.date_start >= now
        ),
        key=lambda visit: visit.date_start,
    )
    past = [visit for visit in visits if visit not in upcoming]
    return ClientPortalVisitsOut(
        upcoming=[out(visit) for visit in upcoming],
        past=[out(visit) for visit in past],
        next_offset=next_offset,
        booking_slug=settings.slug if settings else None,
        booking_enabled=bool(settings and settings.is_enabled),
        currency=business.currency,
        timezone=business.timezone,
    )


@router.post(
    "/visits/{visit_id}/manage-link",
    response_model=ClientPortalManageLinkOut,
    summary="Issue a guest management token for one of the client's visits",
)
def create_portal_manage_link(
    visit_id: int,
    session: ClientPortalSession = Depends(deps.get_portal_session),
    db: Session = Depends(deps.get_db),
    locale: str = Depends(deps.get_locale),
):
    visit = (
        db.query(Visit)
        .filter(
            Visit.id == visit_id,
            Visit.business_id == session.business_id,
            Visit.client_id == session.client_id,
            Visit.deleted_at.is_(None),
        )
        .first()
    )
    if visit is None:
        raise HTTPException(status_code=404, detail=_("portal_visit_not_found", locale))
    raw_token, token = booking_manage_access.issue_token(db, visit)
    return ClientPortalManageLinkOut(token=raw_token, expires_at=token.expires_at)


@router.get(
    "/loyalty",
    response_model=ClientPortalLoyaltyOut,
    summary="Read the client's own points and level",
)
def read_portal_loyalty(
    session: ClientPortalSession = Depends(deps.get_portal_session),
    db: Session = Depends(deps.get_db),
):
    relation = (
        db.query(ClientBusinessRelation)
        .filter(
            ClientBusinessRelation.client_id == session.client_id,
            ClientBusinessRelation.business_id == session.business_id,
        )
        .first()
    )
    wallet_payload = balance_payload(db, session.client_id, session.business_id)
    if relation is None:
        return ClientPortalLoyaltyOut(points=0.0, **wallet_payload)
    level = db.get(LoyaltyLevel, relation.loyalty_level_id) if relation.loyalty_level_id else None
    return ClientPortalLoyaltyOut(
        points=relation.loyalty_points or 0.0,
        **wallet_payload,
        level_name=level.name if level else None,
        level_benefits=level.benefits if level else None,
    )


@router.get(
    "/questionnaires",
    response_model=ClientPortalQuestionnairesOut,
    summary="List questionnaires the client submitted",
)
def list_portal_questionnaires(
    session: ClientPortalSession = Depends(deps.get_portal_session),
    db: Session = Depends(deps.get_db),
):
    rows = (
        db.query(QuestionnaireResponse, QuestionnaireVersion)
        .join(QuestionnaireVersion, QuestionnaireVersion.id == QuestionnaireResponse.version_id)
        .filter(
            QuestionnaireResponse.business_id == session.business_id,
            QuestionnaireResponse.client_id == session.client_id,
        )
        .order_by(QuestionnaireResponse.created_at.desc())
        .limit(100)
        .all()
    )
    return ClientPortalQuestionnairesOut(
        items=[
            ClientPortalQuestionnaireOut(
                id=response.id,
                visit_id=response.visit_id,
                name=version.name,
                version=version.version,
                submitted_at=response.updated_at or response.created_at,
                fields=version.fields or [],
                answers=response.answers or {},
            )
            for response, version in rows
        ]
    )
