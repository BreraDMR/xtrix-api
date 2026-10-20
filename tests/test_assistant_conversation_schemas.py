"""Chat input cannot choose an owner; history keeps typed plans and UTC times."""
from datetime import datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.schemas.assistant_conversation import (
    ConversationCreate, ConversationOut, ConversationRename,
    ConversationPage, MessageOut, MessagePage,
)


def test_create_requires_idempotency_key_and_rejects_owner_fields():
    request_id = str(uuid4())
    body = ConversationCreate(client_request_id=request_id, title='  First chat  ')
    assert str(body.client_request_id) == request_id
    assert body.title == 'First chat'
    for field in ['owner_user_id', 'business_id', 'id', 'archived_at']:
        with pytest.raises(ValidationError):
            ConversationCreate(client_request_id=request_id, **{field: 9})
    with pytest.raises(ValidationError):
        ConversationCreate(title='Missing request key')


@pytest.mark.parametrize('title', ['', '   ', 'x' * 161, 123])
def test_rename_rejects_empty_overlong_and_non_string_titles(title):
    with pytest.raises(ValidationError):
        ConversationRename(title=title)


def test_history_serializes_uuid_and_naive_utc_without_private_owner_ids():
    now = datetime(2026, 10, 20, 12, 0)
    conversation = ConversationOut(id=uuid4(), title='Chat', created_at=now, updated_at=now)
    message = MessageOut(id=uuid4(), conversation_id=conversation.id, ordinal=1,
                         request_id=uuid4(), role='user', text='Hello', status='succeeded', created_at=now)
    page = MessagePage(items=[message], next_before=None)
    result = page.model_dump(mode='json')
    assert result['items'][0]['created_at'] == '2026-10-20T12:00:00Z'
    assert result['items'][0]['plan'] is None
    assert result['items'][0]['result'] is None
    assert 'owner_user_id' not in conversation.model_dump()
    assert ConversationPage(items=[conversation], total=1).total == 1


@pytest.mark.parametrize('change', [{'role': 'system'}, {'ordinal': 0}, {'status': 'delivered'}, {'request_id': 'junk'}])
def test_history_rejects_invalid_roles_statuses_and_order(change):
    data = dict(id=uuid4(), conversation_id=uuid4(), ordinal=1, request_id=uuid4(),
                role='assistant', text='Ready', status='pending', created_at=datetime(2026, 10, 20))
    with pytest.raises(ValidationError):
        MessageOut(**{**data, **change})
