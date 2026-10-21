"""Private saved conversations for the signed-in staff member."""
import hashlib
import json

from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from app.api import deps
from app.api.core.clock import utcnow
from app.api.core.i18n import _
from app.api.crud.crud_assistant_conversation import (
    conversation_error,
    conversation_scope,
    resolve_conversation,
    scoped_conversations,
)
from app.api.middleware.rate_limit import ASSISTANT_PLAN_LIMIT, limiter, user_or_ip
from app.db.models.assistant_conversation import AssistantConversation, AssistantMessage
from app.db.models.user import User
from app.schemas.assistant_conversation import (
    ConversationCreate,
    ConversationOut,
    ConversationPage,
    ConversationRename,
    MessagePage,
)

router = APIRouter(prefix='/conversations')
staff_user = deps.role_required('admin', 'business_owner', 'specialist', 'employee')


@router.get('', response_model=ConversationPage,
            summary='List active private conversations for the signed-in staff member')
def list_conversations(
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0, le=1_000_000),
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(staff_user),
    locale: str = Depends(deps.get_locale),
):
    conversation_scope(db, current_user, locale)
    query = scoped_conversations(db, current_user).filter(AssistantConversation.archived_at.is_(None))
    total = query.count()
    items = query.order_by(
        AssistantConversation.updated_at.desc(),
        AssistantConversation.id.desc(),
    ).offset(offset).limit(limit).all()
    return ConversationPage(items=items, total=total, limit=limit, offset=offset)


@router.get('/{conversation_id}/messages', response_model=MessagePage,
            summary='List ordered messages of a private conversation')
def list_messages(
    conversation_id: UUID,
    before: Optional[int] = Query(None, ge=1),
    limit: int = Query(50, ge=1, le=100),
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(staff_user),
    locale: str = Depends(deps.get_locale),
):
    resolve_conversation(db, current_user, conversation_id, locale=locale, allow_archived=True)
    query = db.query(AssistantMessage).filter(AssistantMessage.conversation_id == conversation_id)
    if before is not None:
        query = query.filter(AssistantMessage.ordinal < before)
    messages = query.order_by(AssistantMessage.ordinal.desc()).limit(limit).all()
    items = list(reversed(messages))
    next_before = None
    if len(messages) == limit and messages[-1].ordinal > 1:
        next_before = messages[-1].ordinal
    return MessagePage(items=items, next_before=next_before)


@router.patch('/{conversation_id}', response_model=ConversationOut,
              summary='Rename a private conversation')
def rename_conversation(
    conversation_id: UUID,
    body: ConversationRename,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(staff_user),
    locale: str = Depends(deps.get_locale),
):
    row = resolve_conversation(db, current_user, conversation_id, locale=locale, lock=True)
    row.title = body.title
    row.updated_at = utcnow()
    db.commit()
    db.refresh(row)
    return row


@router.post('/{conversation_id}/archive', response_model=ConversationOut,
             summary='Archive a private conversation reversibly')
def archive_conversation(
    conversation_id: UUID,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(staff_user),
    locale: str = Depends(deps.get_locale),
):
    row = resolve_conversation(db, current_user, conversation_id, locale=locale, allow_archived=True, lock=True)
    if row.archived_at is None:
        row.archived_at = utcnow()
        row.updated_at = utcnow()
        db.commit()
        db.refresh(row)
    return row


@router.post('', response_model=ConversationOut, status_code=201,
             summary='Create a private conversation; a repeated request key returns the same chat')
@limiter.limit(ASSISTANT_PLAN_LIMIT, key_func=user_or_ip)
def create_conversation(request: Request, body: ConversationCreate,
                        db: Session = Depends(deps.get_db), current_user: User = Depends(staff_user),
                        locale: str = Depends(deps.get_locale)):
    # Serialize creates for this owner, including the first request when no chat row exists yet.
    db.query(User).filter(User.id == current_user.id).populate_existing().with_for_update().one()
    business_id = conversation_scope(db, current_user, locale)
    fingerprint = hashlib.sha256(json.dumps({'title': body.title}, ensure_ascii=False,
                                             sort_keys=True).encode()).hexdigest()
    existing = db.query(AssistantConversation).filter(
        AssistantConversation.owner_user_id == current_user.id,
        AssistantConversation.client_request_id == body.client_request_id).first()
    if existing:
        if existing.business_id != business_id or existing.request_hash != fingerprint:
            raise conversation_error('request_conflict', 409, locale)
        db.commit()
        return existing
    now = utcnow()
    row = AssistantConversation(owner_user_id=current_user.id, business_id=business_id,
        client_request_id=body.client_request_id, request_hash=fingerprint,
        title=body.title or _('assistant_new_conversation', locale), created_at=now, updated_at=now)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row
