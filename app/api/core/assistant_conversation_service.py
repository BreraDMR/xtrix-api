"""Service layer for conversation message persistence, model context and execution links."""
import hashlib
import json
from typing import Optional, Tuple
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from app.ai.plan import MAX_ACTIONS
from app.api.core.clock import utcnow
from app.api.core.i18n import _
from app.api.core.permissions import get_staff_record, get_user_business_id
from app.api.crud import crud_subscription
from app.api.crud.crud_assistant_conversation import conversation_error, conversation_scope, resolve_conversation
from app.db.models.assistant_conversation import AssistantConversation, AssistantMessage
from app.db.models.user import User

MAX_HISTORY_MESSAGES = 6
MAX_HISTORY_CHARS = 1800
MAX_HISTORY_QUESTION_CHARS = 1500


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


_CONTEXT_ACTIONS = frozenset({
    'find_client', 'create_client', 'update_client', 'find_free_slot',
    'create_visit', 'reschedule_visit', 'cancel_visit', 'add_note',
})
_CONTEXT_FAILURES = frozenset({
    'client_not_found', 'ambiguous', 'no_slot', 'conflict', 'not_allowed',
    'not_found', 'bad_request', 'invalid', 'correction_required',
})


def _execution_context(message: AssistantMessage) -> Optional[dict]:
    """Only bounded outcome facts go to the model, never result data or error text."""
    payload = message.result_payload
    if payload is None:
        if message.execution_started_at is not None:
            return {'status': 'unknown'}
        return {'status': 'not_started'} if message.plan_payload else None
    unknown = {'status': 'unknown'}
    plan = message.plan_payload
    if not isinstance(payload, dict) or not isinstance(plan, dict):
        return unknown
    results, actions = payload.get('results'), plan.get('actions')
    if not isinstance(results, list) or not isinstance(actions, list):
        return unknown
    if len(results) != len(actions) or len(results) > MAX_ACTIONS:
        return unknown
    safe_results = []
    failed_index = None
    done_count = 0
    for index, (result, action) in enumerate(zip(results, actions)):
        if not isinstance(result, dict) or not isinstance(action, dict):
            return unknown
        kind, status = result.get('action'), result.get('status')
        if not isinstance(kind, str) or kind not in _CONTEXT_ACTIONS or kind != action.get('action'):
            return unknown
        if type(result.get('index')) is not int or result['index'] != index:
            return unknown
        expected = ('skipped',) if failed_index is not None else ('done', 'failed')
        if status not in expected:
            return unknown
        item = {'index': index, 'action': kind, 'status': status}
        if status == 'done':
            done_count += 1
        elif status == 'failed':
            failed_index = index
            error = result.get('error')
            code = error.get('code') if isinstance(error, dict) else None
            item['reason'] = code if isinstance(code, str) and code in _CONTEXT_FAILURES else 'action_failed'
        safe_results.append(item)
    succeeded = failed_index is None
    if type(payload.get('ok')) is not bool or payload['ok'] != succeeded:
        return unknown
    return {'status': 'succeeded' if succeeded else 'failed',
            'done_count': done_count, 'results': safe_results}


# what a step of an unconfirmed plan carries into the next turn: enough to build it again
# with one detail changed, nothing from the database that the plan didn't already show
_STEP_FIELDS = ('action', 'ref', 'client_id', 'client_ref', 'slot_ref', 'name', 'full_name', 'phone', 'email',
                'contact_phone', 'contact_email', 'specialist_id', 'service_id', 'visit_id', 'start',
                'not_before', 'duration_minutes', 'note', 'text', 'reason')
_STEP_NAMES = ('client_name', 'service_name', 'specialist_name')
_STEP_TEXT_MAX = 120


def _plan_steps(plan_payload) -> Optional[list]:
    """The steps of a shown plan, trimmed, or None when the stored payload looks wrong."""
    actions = plan_payload.get('actions') if isinstance(plan_payload, dict) else None
    if not isinstance(actions, list) or not actions or len(actions) > MAX_ACTIONS:
        return None
    steps = []
    for action in actions:
        if not isinstance(action, dict) or action.get('action') not in _CONTEXT_ACTIONS:
            return None
        step = {}
        for field in _STEP_FIELDS:
            value = action.get(field)
            if isinstance(value, bool) or value is None:
                continue
            if isinstance(value, int) or (isinstance(value, str) and value):
                step[field] = value[:_STEP_TEXT_MAX] if isinstance(value, str) else value
        display = action.get('display')
        if isinstance(display, dict):
            for field in _STEP_NAMES:
                value = display.get(field)
                if isinstance(value, str) and value:
                    step[field] = value[:_STEP_TEXT_MAX]
        steps.append(step)
    return steps


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
        or_(AssistantMessage.status == 'succeeded',
            and_(AssistantMessage.status == 'failed', AssistantMessage.result_payload.isnot(None))),
        AssistantMessage.request_id != current_request_id,
    ).order_by(AssistantMessage.ordinal.desc()).limit(MAX_HISTORY_MESSAGES).all()

    history = []
    total_chars = 0
    question_chars = 0
    for m in messages:
        snippet = m.text[:300] if m.text else ''
        if total_chars + len(snippet) > MAX_HISTORY_CHARS:
            break
        item = {'role': m.role, 'text': snippet}
        # Keep the exact recent questions as well as the short narrative summary.
        # A separate budget preserves the existing six-message text allowance.
        if m.role == 'assistant' and isinstance(m.plan_payload, dict):
            questions = m.plan_payload.get('questions')
            if isinstance(questions, list):
                included = []
                for question in questions[:5]:
                    if not isinstance(question, str) or not 1 <= len(question) <= 300:
                        continue
                    if question_chars + len(question) > MAX_HISTORY_QUESTION_CHARS:
                        continue
                    included.append(question)
                    question_chars += len(question)
                if included:
                    item['questions'] = included
        if m.role == 'assistant':
            execution = _execution_context(m)
            if execution is not None:
                item['execution'] = execution
            # only the latest plan, and only while it never ran: "no, at 14" rebuilds it, nothing to move
            latest = not any(h['role'] == 'assistant' for h in history)
            if latest and execution == {'status': 'not_started'}:
                steps = _plan_steps(m.plan_payload)
                if steps:
                    item['plan'] = steps
        history.append(item)
        total_chars += len(snippet)
    history.reverse()
    return history
