"""Service layer for conversation message persistence, model context and execution links."""
import hashlib
import json
from typing import Optional, Tuple
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.api.core.clock import utcnow
from app.api.core.i18n import _
from app.api.core.permissions import get_staff_record, get_user_business_id
from app.api.crud import crud_subscription
from app.api.crud.crud_assistant_conversation import conversation_error, conversation_scope, resolve_conversation
from app.db.models.assistant_conversation import AssistantConversation, AssistantMessage
from app.db.models.user import User

MAX_HISTORY_MESSAGES = 6
MAX_HISTORY_CHARS = 1800


def generation_scope_snapshot(db: Session, user: User, locale: str = 'en') -> str:
    """Read current employment and payer state, never a request's cached ORM values."""
    fresh = db.query(User).filter(User.id == user.id).populate_existing().first()
    if fresh is None or fresh.deleted_at is not None or fresh.is_locked:
        raise conversation_error('request_conflict', 409, locale)
    staff = get_staff_record(db, fresh)
    owner = None
    if staff:
        owner = db.query(User).filter(User.id == staff.business_owner_id).populate_existing().first()
    try:
        conv_scope = conversation_scope(db, fresh, locale)
    except HTTPException:
        raise conversation_error('request_conflict', 409, locale)
    perm_scope = get_user_business_id(fresh, db)
    if not (conv_scope == perm_scope == fresh.business_id):
        raise conversation_error('request_conflict', 409, locale)
    values = [fresh.id, fresh.role, fresh.business_id, fresh.employer_id,
              crud_subscription.subscription_payer_id(db, fresh)]
    if staff:
        values += [staff.id, staff.business_owner_id, staff.is_active, staff.permissions,
                   owner.id if owner else None, owner.business_id if owner else None,
                   owner.role if owner else None, str(owner.deleted_at) if owner else None]
    return hashlib.sha256(json.dumps(values, sort_keys=True).encode()).hexdigest()


def validate_generation_scope(db: Session, user: User, locale: str = 'en',
                              expected_scope: Optional[str] = None) -> Optional[int]:
    snapshot = generation_scope_snapshot(db, user, locale)
    if expected_scope is not None and snapshot != expected_scope:
        raise conversation_error('request_conflict', 409, locale)
    return get_user_business_id(user, db)


def prepare_user_request(
    db: Session,
    user: User,
    conversation_id: UUID,
    client_request_id: UUID,
    text: str,
    locale: str = 'en',
    expected_scope: Optional[str] = None,
) -> Tuple[AssistantConversation, AssistantMessage, Optional[AssistantMessage]]:
    """Locks the conversation, validates scope, and ensures the user message is saved.
    
    Returns (conversation, user_message, existing_assistant_message).
    If assistant message already succeeded or failed, it is returned for replay.
    """
    validate_generation_scope(db, user, locale, expected_scope)
    conv = resolve_conversation(db, user, conversation_id, locale=locale, lock=True)
    # A membership may have changed while the conversation row lock was waiting.
    validate_generation_scope(db, user, locale, expected_scope)
    text_hash = hashlib.sha256(text.encode('utf-8')).hexdigest()

    existing_user_msg = db.query(AssistantMessage).filter(
        AssistantMessage.conversation_id == conv.id,
        AssistantMessage.request_id == client_request_id,
        AssistantMessage.role == 'user',
    ).first()

    if existing_user_msg:
        if existing_user_msg.request_hash != text_hash:
            raise conversation_error('request_conflict', 409, locale)
        existing_assistant_msg = db.query(AssistantMessage).filter(
            AssistantMessage.conversation_id == conv.id,
            AssistantMessage.request_id == client_request_id,
            AssistantMessage.role == 'assistant',
        ).first()
        if existing_assistant_msg and existing_assistant_msg.status == 'pending':
            raise conversation_error('request_in_progress', 409, locale)
        return conv, existing_user_msg, existing_assistant_msg

    now = utcnow()
    user_ordinal = conv.next_ordinal
    assistant_ordinal = conv.next_ordinal + 1
    conv.next_ordinal += 2
    conv.updated_at = now

    user_msg = AssistantMessage(
        conversation_id=conv.id,
        ordinal=user_ordinal,
        request_id=client_request_id,
        request_hash=text_hash,
        role='user',
        text=text,
        status='succeeded',
        created_at=now,
    )
    db.add(user_msg)

    assistant_msg = AssistantMessage(
        conversation_id=conv.id,
        ordinal=assistant_ordinal,
        request_id=client_request_id,
        request_hash=text_hash,
        role='assistant',
        text='',
        status='pending',
        created_at=now,
    )
    db.add(assistant_msg)
    db.commit()
    db.refresh(user_msg)
    db.refresh(assistant_msg)
    return conv, user_msg, assistant_msg


def record_assistant_success(
    db: Session,
    conversation_id: UUID,
    assistant_msg_id: UUID,
    summary: str,
    plan_id: str,
    plan_dict: dict,
    expires_at,
) -> None:
    now = utcnow()
    db.query(AssistantMessage).filter(AssistantMessage.id == assistant_msg_id).update({
        'status': 'succeeded',
        'text': summary,
        'plan_id': plan_id,
        'plan_payload': plan_dict,
        'expires_at': expires_at,
    })
    db.query(AssistantConversation).filter(AssistantConversation.id == conversation_id).update({
        'updated_at': now,
    })
    db.commit()


def record_assistant_failure(
    db: Session,
    conversation_id: UUID,
    assistant_msg_id: UUID,
    code: str,
    status: int,
    retry_after: Optional[int] = None,
) -> None:
    now = utcnow()
    db.query(AssistantMessage).filter(AssistantMessage.id == assistant_msg_id).update({
        'status': 'failed',
        'error_code': code,
        'error_status': status,
        'error_retry_after': retry_after,
    })
    db.query(AssistantConversation).filter(AssistantConversation.id == conversation_id).update({
        'updated_at': now,
    })
    db.commit()


def get_conversation_context(
    db: Session,
    conversation_id: UUID,
    current_request_id: UUID,
) -> list[dict]:
    """Retrieves limited recent history of this conversation for model context.
    
    Excludes pending messages and the current request, keeping budget bounded.
    """
    messages = db.query(AssistantMessage).filter(
        AssistantMessage.conversation_id == conversation_id,
        AssistantMessage.status == 'succeeded',
        AssistantMessage.request_id != current_request_id,
    ).order_by(AssistantMessage.ordinal.desc()).limit(MAX_HISTORY_MESSAGES).all()

    messages.reverse()
    history = []
    total_chars = 0
    for m in messages:
        snippet = m.text[:300] if m.text else ''
        if total_chars + len(snippet) > MAX_HISTORY_CHARS:
            break
        history.append({'role': m.role, 'text': snippet})
        total_chars += len(snippet)
    return history
