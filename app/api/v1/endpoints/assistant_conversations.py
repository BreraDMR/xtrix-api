"""Private saved conversations for the signed-in staff member."""
import hashlib
import json

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.api import deps
from app.api.core.clock import utcnow
from app.api.core.i18n import _
from app.api.crud.crud_assistant_conversation import conversation_error, conversation_scope
from app.api.middleware.rate_limit import ASSISTANT_PLAN_LIMIT, limiter, user_or_ip
from app.db.models.assistant_conversation import AssistantConversation
from app.db.models.user import User
from app.schemas.assistant_conversation import ConversationCreate, ConversationOut

router = APIRouter(prefix='/conversations')
staff_user = deps.role_required('admin', 'business_owner', 'specialist', 'employee')


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
