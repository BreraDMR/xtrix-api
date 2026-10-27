"""The real migration and database constraints protect chat identity and ordering."""
from uuid import uuid4

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
import pytest
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError

from app.db.models.assistant_conversation import AssistantConversation, AssistantMessage
from app.db.models.user import User


def conversation(db, owner, **overrides):
    data = dict(owner_user_id=owner.id, title='Private chat', client_request_id=uuid4(), request_hash='a' * 64)
    row = AssistantConversation(**{**data, **overrides})
    db.add(row)
    db.commit()
    return row


def message(db, chat, **overrides):
    data = dict(conversation_id=chat.id, ordinal=1, request_id=uuid4(), request_hash='b' * 64,
                role='user', text='Hello', status='succeeded')
    row = AssistantMessage(**{**data, **overrides})
    db.add(row)
    db.commit()
    return row


def test_request_key_does_not_duplicate_a_solo_chat(db, make_user):
    owner = make_user()
    chat = conversation(db, owner)
    with pytest.raises(IntegrityError):
        conversation(db, owner, client_request_id=chat.client_request_id)
    db.rollback()
    assert db.query(AssistantConversation).count() == 1


def test_different_users_can_use_the_same_create_request_key(db, make_user):
    first = conversation(db, make_user())
    second = conversation(db, make_user(), client_request_id=first.client_request_id)
    assert first.id != second.id


@pytest.mark.parametrize('duplicate', ['ordinal', 'request_role'])
def test_database_refuses_duplicate_order_or_request_role(db, make_user, duplicate):
    chat = conversation(db, make_user())
    first = message(db, chat)
    payload = {'ordinal': first.ordinal} if duplicate == 'ordinal' else {
        'ordinal': 2, 'request_id': first.request_id, 'role': first.role,
    }
    with pytest.raises(IntegrityError):
        message(db, chat, **payload)
    db.rollback()
    assert db.query(AssistantMessage).count() == 1


def test_request_has_a_user_and_assistant_with_stable_order(db, make_user):
    chat = conversation(db, make_user())
    first = message(db, chat)
    reply = message(db, chat, ordinal=2, request_id=first.request_id, role='assistant', status='pending')
    reply.created_at = first.created_at
    db.commit()
    rows = db.query(AssistantMessage).order_by(AssistantMessage.ordinal).all()
    assert [r.id for r in rows] == [first.id, reply.id]


@pytest.mark.parametrize('change', [{'role': 'system'}, {'status': 'delivered'}, {'ordinal': 0}])
def test_database_rejects_invalid_message_state(db, make_user, change):
    chat = conversation(db, make_user())
    with pytest.raises(IntegrityError):
        message(db, chat, **change)
    db.rollback()


def test_migration_roundtrip_preserves_existing_users(db, make_user):
    user_id = make_user().id
    # make_user refreshes the row; release its read lock before DDL on FK tables.
    db.rollback()
    config = Config('alembic.ini')
    assert ScriptDirectory.from_config(config).get_heads() == ['0082_visit_currency_snapshot']
    try:
        command.downgrade(config, '0044_stripe_checkout')
        assert not inspect(db.get_bind()).has_table('assistant_conversations')
        assert db.query(User).filter(User.id == user_id).count() == 1
    finally:
        db.rollback()
        command.upgrade(config, 'head')
    assert inspect(db.get_bind()).has_table('assistant_messages')
    assert db.query(User).filter(User.id == user_id).count() == 1


def test_empty_optional_payloads_are_sql_null(db, make_user):
    chat = conversation(db, make_user())
    row = message(db, chat, role='assistant', plan_payload=None, result_payload=None)
    assert db.query(AssistantMessage).filter(AssistantMessage.id == row.id,
        AssistantMessage.plan_payload.is_(None), AssistantMessage.result_payload.is_(None)).count() == 1
