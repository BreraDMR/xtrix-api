"""A lost response must not turn a recorded assistant failure into a raw code."""
from datetime import timedelta
from uuid import uuid4

import pytest

from app.ai import assistant
from app.ai.errors import AIBusy, AILimitReached, AIUnavailable
from app.api.core import assistant_conversation_service as conversations
from app.api.core.clock import utcnow
from app.api.core.i18n import _ as translate
from app.api.v1.endpoints import assistant as endpoints
from tests import test_assistant_api as api

ab = api.ab
fakes = api.fakes


@pytest.mark.parametrize('locale', ['ru', 'cz', 'en'])
@pytest.mark.parametrize('failure,key', [
    (AIBusy(42), 'ai_busy'),
    (AIUnavailable('PRIVATE_PROVIDER_DIAGNOSTIC'), 'ai_unavailable'),
    (AILimitReached(50, 50, 42, period='day'), 'ai_daily_limit_reached'),
    (AILimitReached(50, 50, 42), 'ai_limit_reached'),
])
def test_failure_replay_keeps_localized_guidance_and_remaining_wait(
    client, ab, fakes, monkeypatch, locale, failure, key,
):
    _, tenant = ab
    now = utcnow()
    monkeypatch.setattr(conversations, 'utcnow', lambda: now)
    chat = api.conversation(client, tenant)
    request_id = str(uuid4())
    calls = []

    def fail(*args, **kwargs):
        calls.append(True)
        raise failure

    monkeypatch.setattr(assistant, 'make_plan', fail)
    first = api.ask(client, tenant, 'Запиши клиента', locale='en',
                    conversation_id=chat, client_request_id=request_id)
    assert first.status_code in (429, 503), first.text
    # Replay can be in another language after a lost HTTP response.
    monkeypatch.setattr(endpoints, 'utcnow', lambda: now + timedelta(seconds=10))
    replay = api.ask(client, tenant, 'Запиши клиента', locale=locale,
                     conversation_id=chat, client_request_id=request_id)
    assert replay.status_code == first.status_code
    assert api.error(replay)['message'] == translate(key, locale)
    assert api.error(replay)['detail']['code'] == failure.code
    if isinstance(failure, AILimitReached):
        for field in ('limit', 'used', 'period'):
            assert api.error(replay)['detail'][field] == getattr(failure, field)
    if isinstance(failure, (AIBusy, AILimitReached)):
        assert replay.headers['retry-after'] == '32'
    else:
        assert 'retry-after' not in replay.headers
    assert calls == [True]
    assert 'PRIVATE_PROVIDER_DIAGNOSTIC' not in replay.text


def test_expired_wait_does_not_restart_on_failure_replay(client, ab, fakes, monkeypatch):
    _, tenant = ab
    now = utcnow()
    monkeypatch.setattr(conversations, 'utcnow', lambda: now)
    chat, request_id = api.conversation(client, tenant), str(uuid4())

    def fail(*args, **kwargs):
        raise AIBusy(42)

    monkeypatch.setattr(assistant, 'make_plan', fail)
    first = api.ask(client, tenant, conversation_id=chat, client_request_id=request_id)
    assert first.status_code == 503
    monkeypatch.setattr(endpoints, 'utcnow', lambda: now + timedelta(seconds=50))
    replay = api.ask(client, tenant, conversation_id=chat, client_request_id=request_id)
    assert replay.headers['retry-after'] == '0'


@pytest.mark.parametrize('code,status,key', [
    ('ai_busy', 503, 'ai_busy'),
    ('ai_limit_reached', 429, 'ai_saved_limit_reached'),
    ('server_error', 500, 'ai_unavailable'),
    ('invalid_request', 422, 'ai_unavailable'),
])
def test_legacy_failure_without_payload_remains_readable(client, db, ab, fakes, monkeypatch, code, status, key):
    from app.db.models.assistant_conversation import AssistantMessage
    _, tenant = ab
    chat, request_id = api.conversation(client, tenant), str(uuid4())
    calls = []

    def fail(*args, **kwargs):
        calls.append(True)
        raise AIBusy(42)

    monkeypatch.setattr(assistant, 'make_plan', fail)
    first = api.ask(client, tenant, conversation_id=chat, client_request_id=request_id)
    assert first.status_code == 503
    message = db.query(AssistantMessage).filter_by(role='assistant').one()
    message.error_code, message.error_status, message.error_payload = code, status, None
    db.commit()
    replay = api.ask(client, tenant, locale='cz', conversation_id=chat, client_request_id=request_id)
    assert replay.status_code == status
    assert api.error(replay)['message'] == translate(key, 'cz')
    assert replay.headers['retry-after'] == '42'
    assert calls == [True]
