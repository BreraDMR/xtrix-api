"""Execution outcomes must survive into the next conversation turn without raw result data."""
import json
from uuid import uuid4

import pytest

from tests import test_assistant_api as api

ab = api.ab
fakes = api.fakes


def history(fakes):
    block = fakes.payloads[-1]['messages'][1]['content'].split('<data>', 1)[1].rsplit('</data>', 1)[0]
    return json.loads(block)['context']['history']


def vanishing_client(client, tenant, name='Исчезающая Клиентка'):
    # a card that exists while the plan is made and is archived before it's confirmed, so the
    # plan passes review and find_client really fails at execution
    r = client.post('/clients/', json={'full_name': name}, headers=tenant['headers'])
    assert r.status_code == 200, r.text
    return r.json()


def archive(db, client_id):
    from app.api.core.clock import utcnow
    from app.db.models.client import Client
    db.get(Client, client_id).archived_at = utcnow()
    db.commit()


def failed_plan(client, tenant, fakes, chat, request_id):
    from app.db.session import SessionLocal
    gone = vanishing_client(client, tenant)
    fakes.answers.append(api.plan_of(
        {'action': 'find_client', 'name': gone['full_name'], 'ref': 'client'},
        {'action': 'create_visit', 'client_ref': 'client', 'start': '2027-01-10T10:00:00Z'},
        summary='Найду клиента и запишу'))
    draft = api.ask(client, tenant, 'Запиши клиента', conversation_id=chat, client_request_id=request_id)
    assert draft.status_code == 200, draft.text
    with SessionLocal() as db:
        archive(db, gone['id'])
    result = api.run(client, tenant, draft.json()['plan_id'])
    assert result.status_code == 200 and result.json()['ok'] is False, result.text
    return draft, result


def test_failed_execution_reaches_next_turn_with_failure_and_skipped_actions(client, ab, fakes):
    _, tenant = ab
    chat = api.conversation(client, tenant)
    failed_plan(client, tenant, fakes, chat, str(uuid4()))
    followup = api.ask(client, tenant, 'Тогда уточни имя', conversation_id=chat, client_request_id=str(uuid4()))
    assert followup.status_code == 200, followup.text
    items = [item for item in history(fakes) if item['role'] == 'assistant']
    assert len(items) == 1
    assert items[0]['execution'] == {
        'status': 'failed', 'done_count': 0, 'results': [
            {'index': 0, 'action': 'find_client', 'status': 'failed', 'reason': 'client_not_found'},
            {'index': 1, 'action': 'create_visit', 'status': 'skipped'},
        ]}


def test_plan_request_replays_the_same_generated_plan_after_execution_failure(client, ab, fakes):
    _, tenant = ab
    chat, request_id = api.conversation(client, tenant), str(uuid4())
    draft, result = failed_plan(client, tenant, fakes, chat, request_id)
    calls = fakes.model_calls
    replay = api.ask(client, tenant, 'Запиши клиента', conversation_id=chat, client_request_id=request_id)
    assert replay.status_code == 200, replay.text
    assert replay.json() == draft.json()
    assert fakes.model_calls == calls
    assert api.run(client, tenant, draft.json()['plan_id']).json() == result.json()


def test_completed_execution_feedback_excludes_raw_data_and_error_messages(client, db, ab, fakes):
    from app.db.models.assistant_conversation import AssistantMessage
    _, tenant = ab
    chat = api.conversation(client, tenant)
    fakes.answers.append(api.plan_of({'action': 'find_client', 'name': tenant['client']['full_name']},
                                    summary='Найду клиента'))
    draft = api.ask(client, tenant, 'Найди клиента', conversation_id=chat, client_request_id=str(uuid4()))
    result = api.run(client, tenant, draft.json()['plan_id'])
    assert result.status_code == 200 and result.json()['ok'], result.text
    message = db.query(AssistantMessage).filter_by(plan_id=draft.json()['plan_id']).one()
    payload = result.json()
    payload['results'][0]['data'] = {'medical_note': 'PRIVATE_SENTINEL', 'token': 'SECRET_SENTINEL'}
    payload['results'][0]['error'] = {'message': 'PRIVATE_SENTINEL', 'code': 'SECRET_SENTINEL'}
    message.result_payload = payload
    db.commit()
    followup = api.ask(client, tenant, 'Что получилось?', conversation_id=chat, client_request_id=str(uuid4()))
    assert followup.status_code == 200, followup.text
    item = next(item for item in history(fakes) if item['role'] == 'assistant')
    assert item['execution'] == {'status': 'succeeded', 'done_count': 1,
                                 'results': [{'index': 0, 'action': 'find_client', 'status': 'done'}]}
    assert 'PRIVATE_SENTINEL' not in json.dumps(history(fakes))
    assert 'SECRET_SENTINEL' not in json.dumps(history(fakes))


def test_partial_failure_retains_the_completed_action_in_next_turn(client, db, ab, fakes):
    from app.db.models.client import Client
    _, tenant = ab
    chat = api.conversation(client, tenant)
    gone = vanishing_client(client, tenant)
    fakes.answers.append(api.plan_of(
        {'action': 'create_client', 'full_name': 'Новый клиент после уточнения'},
        {'action': 'find_client', 'name': gone['full_name'], 'ref': 'client'},
        {'action': 'create_visit', 'client_ref': 'client', 'start': '2027-01-10T10:00:00Z'},
        summary='Создам карточку и проверю другого клиента'))
    draft = api.ask(client, tenant, 'Подтверждаю данные', conversation_id=chat, client_request_id=str(uuid4()))
    archive(db, gone['id'])
    result = api.run(client, tenant, draft.json()['plan_id'])
    assert result.status_code == 200 and not result.json()['ok'], result.text
    followup = api.ask(client, tenant, 'Почему запись не появилась?', conversation_id=chat, client_request_id=str(uuid4()))
    assert followup.status_code == 200, followup.text
    item = next(item for item in history(fakes) if item['role'] == 'assistant')
    assert item['execution']['done_count'] == 1
    assert [row['status'] for row in item['execution']['results']] == ['done', 'failed', 'skipped']
    assert db.query(Client).filter_by(full_name='Новый клиент после уточнения').count() == 1


def test_unknown_execution_error_is_reduced_to_a_fixed_reason(client, db, ab, fakes):
    from app.db.models.assistant_conversation import AssistantMessage
    _, tenant = ab
    chat = api.conversation(client, tenant)
    draft, result = failed_plan(client, tenant, fakes, chat, str(uuid4()))
    message = db.query(AssistantMessage).filter_by(plan_id=draft.json()['plan_id']).one()
    payload = result.json()
    payload['results'][0]['error'] = {'code': 'PRIVATE_SENTINEL' * 500, 'message': 'INTERNAL_SENTINEL'}
    payload['results'][0]['data'] = {'candidates': [{'contact_phone': 'PRIVATE_SENTINEL'}]}
    message.result_payload = payload
    db.commit()
    followup = api.ask(client, tenant, 'Продолжи', conversation_id=chat, client_request_id=str(uuid4()))
    assert followup.status_code == 200, followup.text
    execution = next(item for item in history(fakes) if item['role'] == 'assistant')['execution']
    assert execution['results'][0]['reason'] == 'action_failed'
    assert 'SENTINEL' not in json.dumps(history(fakes))


def test_unexecuted_proposal_is_not_described_as_completed(client, ab, fakes):
    _, tenant = ab
    chat = api.conversation(client, tenant)
    fakes.answers.append(api.plan_of({'action': 'find_client', 'name': 'Анастасия'}, summary='Проверю клиента'))
    draft = api.ask(client, tenant, 'Анастасию на завтра', conversation_id=chat, client_request_id=str(uuid4()))
    assert draft.status_code == 200, draft.text
    followup = api.ask(client, tenant, 'Уже записала?', conversation_id=chat, client_request_id=str(uuid4()))
    assert followup.status_code == 200, followup.text
    item = next(item for item in history(fakes) if item['role'] == 'assistant')
    assert item['execution'] == {'status': 'not_started'}


@pytest.mark.parametrize('change', [
    lambda payload: payload.update(results='PRIVATE_SENTINEL'),
    lambda payload: payload.update(results=payload['results'] * 11),
    lambda payload: payload.update(ok=True),
    lambda payload: payload['results'][0].update(action='PRIVATE_SENTINEL'),
    lambda payload: payload['results'][0].update(status='PRIVATE_SENTINEL'),
    lambda payload: payload['results'][0].update(index=True),
    lambda payload: payload['results'][1].update(status='done'),
])
def test_malformed_execution_metadata_stays_unknown_without_leaking(client, db, ab, fakes, change):
    from app.db.models.assistant_conversation import AssistantMessage
    _, tenant = ab
    chat = api.conversation(client, tenant)
    draft, result = failed_plan(client, tenant, fakes, chat, str(uuid4()))
    message = db.query(AssistantMessage).filter_by(plan_id=draft.json()['plan_id']).one()
    payload = result.json()
    change(payload)
    message.result_payload = payload
    db.commit()
    followup = api.ask(client, tenant, 'Проверь результат', conversation_id=chat, client_request_id=str(uuid4()))
    assert followup.status_code == 200, followup.text
    item = next(item for item in history(fakes) if item['role'] == 'assistant')
    assert item['execution'] == {'status': 'unknown'}
    assert 'PRIVATE_SENTINEL' not in json.dumps(history(fakes))


def test_interrupted_execution_is_unknown_and_is_not_run_by_a_followup(client, db, ab, fakes):
    from app.db.models.assistant_conversation import AssistantMessage
    from app.api.core.clock import utcnow
    _, tenant = ab
    chat = api.conversation(client, tenant)
    fakes.answers.append(api.plan_of({'action': 'create_client', 'full_name': 'Must not be created'}, summary='Создам карточку'))
    draft = api.ask(client, tenant, 'Новый клиент', conversation_id=chat, client_request_id=str(uuid4()))
    message = db.query(AssistantMessage).filter_by(plan_id=draft.json()['plan_id']).one()
    message.execution_started_at = utcnow()
    db.commit()
    followup = api.ask(client, tenant, 'Что получилось?', conversation_id=chat, client_request_id=str(uuid4()))
    assert followup.status_code == 200, followup.text
    item = next(item for item in history(fakes) if item['role'] == 'assistant')
    assert item['execution'] == {'status': 'unknown'}
    from app.db.models.client import Client
    assert db.query(Client).filter_by(full_name='Must not be created').count() == 0
