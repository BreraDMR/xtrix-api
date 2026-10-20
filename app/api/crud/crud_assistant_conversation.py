"""One access resolver for chat endpoints, model requests and confirmations."""
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import false
from sqlalchemy.orm import Session

from app.api.core.i18n import _
from app.api.core.permissions import get_staff_record, get_user_business_id
from app.db.models.assistant_conversation import AssistantConversation
from app.db.models.user import User
from app.db.models.business import Business


def conversation_error(code: str, status: int, locale: str = 'en') -> HTTPException:
    return HTTPException(status, detail={'message': _(code, locale), 'code': code})


def _current_scope(db: Session, user: User):
    if user.role not in ('admin', 'business_owner', 'specialist', 'employee'):
        return False, None
    staff = get_staff_record(db, user)
    if staff:
        owner = db.query(User).filter(User.id == staff.business_owner_id, User.deleted_at.is_(None)).first()
        if owner is None or owner.business_id is None:
            return False, None
        business_id = owner.business_id
    elif user.role == 'employee' or (user.role == 'specialist' and user.employer_id is not None):
        # A disabled Employee can leave the old business_id on the user row.
        return False, None
    else:
        business_id = get_user_business_id(user, db)
    if business_id is not None and not db.query(Business.id).filter(
            Business.id == business_id, Business.deleted_at.is_(None)).first():
        return False, None
    return True, business_id


def conversation_scope(db: Session, user: User, locale: str = 'en'):
    valid, business_id = _current_scope(db, user)
    if not valid:
        raise conversation_error('conversation_not_found', 404, locale)
    return business_id


def scoped_conversations(db: Session, user: User):
    valid, business_id = _current_scope(db, user)
    query = db.query(AssistantConversation).filter(AssistantConversation.owner_user_id == user.id)
    if not valid:
        return query.filter(false())
    if business_id is None:
        return query.filter(AssistantConversation.business_id.is_(None))
    return query.filter(AssistantConversation.business_id == business_id)


def resolve_conversation(db: Session, user: User, conversation_id: UUID, *, locale: str = 'en',
                         allow_archived: bool = False, lock: bool = False) -> AssistantConversation:
    query = scoped_conversations(db, user).filter(AssistantConversation.id == conversation_id)
    if lock:
        query = query.populate_existing().with_for_update(of=AssistantConversation)
    row = query.first()
    if row is None or (row.archived_at is not None and not allow_archived):
        raise conversation_error('conversation_not_found', 404, locale)
    return row
