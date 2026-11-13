"""Dialogue contract probes using synthetic model responses and local data."""
import json
from datetime import datetime, timedelta
from uuid import uuid4
from zoneinfo import ZoneInfo
import pytest
from tests import test_assistant_api as api
from tests.test_assistant_api import ask, conversation, plan_of

ab = api.ab
fakes = api.fakes


def test_followup_receives_the_exact_question_that_was_shown(client, ab, fakes):
    _, tenant = ab
    chat = conversation(client, tenant)
    question = 'Какую Анастасию выбрать: первую или вторую?'
    fakes.answers.extend([plan_of(summary='Нужно уточнить клиента', questions=[question]),
                          plan_of(summary='Проверю свободное время')])
    first = ask(client, tenant, 'Анастасию на завтра', conversation_id=chat,
                client_request_id=str(uuid4()))
    assert first.status_code == 200, first.text
    assert first.json()['questions'] == [question]
    second = ask(client, tenant, 'Вторую', conversation_id=chat,
                 client_request_id=str(uuid4()))
    assert second.status_code == 200, second.text
    block = fakes.payloads[-1]['messages'][1]['content'].split('<data>', 1)[1].rsplit('</data>', 1)[0]
    history = json.loads(block)['context']['history']
    assert any(question in row.get('questions', []) for row in history if row['role'] == 'assistant'), history


@pytest.mark.parametrize('locale,expected', [('ru', 'Уточните'), ('en', 'Please specify'), ('cz', 'Upřesněte')])
def test_execution_failure_has_a_human_message_for_the_next_dialogue(client, db, ab, fakes, locale, expected):
    from tests.test_ai_execution_feedback import archive, vanishing_client
    _, tenant = ab
    gone = vanishing_client(client, tenant, 'Анастасия Исчезающая')
    fakes.answers.append(plan_of({'action': 'find_client', 'ref': 'client',
                                  'name': gone['full_name']}, summary='Найду клиента'))
    draft = ask(client, tenant, 'Анастасию на завтра')
    assert draft.status_code == 200, draft.text
    # the card is archived between the plan and the confirmation
    archive(db, gone['id'])
    result = client.post('/assistant/execute', json={'plan_id': draft.json()['plan_id']},
                         headers={**tenant['headers'], 'Accept-Language': locale})
    assert result.status_code == 200, result.text
    failed = result.json()['results'][0]['error']
    assert failed['code'] == 'client_not_found'
    assert failed['message'] and failed['message'] != failed['code'], failed
    assert expected in failed['message']


def test_incomplete_request_becomes_one_booking_only_after_confirmation(client, db, ab, fakes):
    from app.db.models.client import Client
    from app.db.models.visit import Visit
    _, tenant = ab
    db.get(Client, tenant['client']['id']).full_name = 'Анастасия Петрова'
    db.commit()
    chat = conversation(client, tenant)
    tomorrow = (datetime.now(ZoneInfo('Europe/Prague')) + timedelta(days=1)).replace(
        hour=10, minute=0, second=0, microsecond=0)
    prompts = ['На какую услугу записать Анастасию?', 'К какому мастеру?', 'Во сколько завтра?']
    fakes.answers.extend([plan_of(summary='Уточню услугу', questions=[prompts[0]]),
        plan_of(summary='Уточню мастера', questions=[prompts[1]]),
        plan_of(summary='Уточню время', questions=[prompts[2]]),
        plan_of({'action': 'find_client', 'ref': 'client', 'name': 'Анастасия Петрова'},
                {'action': 'create_visit', 'client_ref': 'client',
                 'specialist_id': tenant['owner'].id, 'service_id': tenant['service']['id'],
                 'start': tomorrow.isoformat()}, summary='Запись готова к подтверждению')])
    before = db.query(Visit).count()
    for index, text in enumerate(['Анастасию на завтра', 'Массаж', 'К владельцу', 'В десять']):
        draft = ask(client, tenant, text, conversation_id=chat, client_request_id=str(uuid4()))
        assert draft.status_code == 200, draft.text
        if index < 3:
            assert draft.json()['questions'] == [prompts[index]]
            assert draft.json()['actions'] == []
        assert db.query(Visit).count() == before
    block = fakes.payloads[-1]['messages'][1]['content'].split('<data>', 1)[1].rsplit('</data>', 1)[0]
    history = json.loads(block)['context']['history']
    assert all(question in json.dumps(history, ensure_ascii=False) for question in prompts)
    payload = {'plan_id': draft.json()['plan_id']}
    result = client.post('/assistant/execute', json=payload, headers=tenant['headers'])
    assert result.status_code == 200 and result.json()['ok'], result.text
    visit_id = result.json()['results'][1]['data']['visit_id']
    created = db.get(Visit, visit_id)
    assert created.client_id == tenant['client']['id']
    assert created.date_start == tomorrow.astimezone(ZoneInfo('UTC')).replace(tzinfo=None)
    replay = client.post('/assistant/execute', json=payload, headers=tenant['headers'])
    assert replay.json() == result.json()
    assert db.query(Visit).count() == before + 1


def test_question_budget_keeps_the_most_recent_exact_choices(client, ab, fakes):
    _, tenant = ab
    chat = conversation(client, tenant)
    latest = []
    for turn in range(3):
        latest = [(f'{turn}-{index}: ' + 'ж' * 300)[:300] for index in range(5)]
        fakes.answers.append(plan_of(summary='Уточню выбор', questions=latest))
        response = ask(client, tenant, f'Запрос {turn}', conversation_id=chat,
                       client_request_id=str(uuid4()))
        assert response.status_code == 200, response.text
    response = ask(client, tenant, 'Последний вариант', conversation_id=chat,
                   client_request_id=str(uuid4()))
    assert response.status_code == 200, response.text
    block = fakes.payloads[-1]['messages'][1]['content'].split('<data>', 1)[1].rsplit('</data>', 1)[0]
    history = json.loads(block)['context']['history']
    included = [question for item in history for question in item.get('questions', [])]
    assert included == latest
    assert sum(map(len, included)) <= 1500
    assert sum(len(item['text']) for item in history) <= 1800


@pytest.mark.parametrize('stored,expected', [
    ('not a list', []),
    ([None, {'not': 'text'}, 'x' * 301, False, 'Настоящий вопрос?'], ['Настоящий вопрос?']),
    (None, []),
])
def test_malformed_saved_question_metadata_does_not_enter_context(client, db, ab, fakes, stored, expected):
    from app.db.models.assistant_conversation import AssistantMessage
    _, tenant = ab
    chat = conversation(client, tenant)
    response = ask(client, tenant, 'Уточнение', conversation_id=chat,
                   client_request_id=str(uuid4()))
    assert response.status_code == 200, response.text
    message = db.query(AssistantMessage).filter_by(role='assistant').one()
    message.plan_payload = {**message.plan_payload, 'questions': stored}
    db.commit()
    response = ask(client, tenant, 'Продолжение', conversation_id=chat,
                   client_request_id=str(uuid4()))
    assert response.status_code == 200, response.text
    block = fakes.payloads[-1]['messages'][1]['content'].split('<data>', 1)[1].rsplit('</data>', 1)[0]
    history = json.loads(block)['context']['history']
    assert [question for item in history for question in item.get('questions', [])] == expected


@pytest.mark.parametrize('status,detail,expected', [
    (400, {'code': 'correction_required', 'message': 'Нужна отдельная корректировка оплаты.'},
     'Нужна отдельная корректировка оплаты.'),
    (400, 'client_not_found', 'Не удалось выполнить действие.'),
    (400, {'code': 'not_allowed', 'message': 'not_allowed'}, 'Недостаточно прав'),
    (500, {'code': 'server_error', 'message': 'INTERNAL_DIAGNOSTIC_DO_NOT_DISPLAY'},
     'Не удалось выполнить действие.'),
])
def test_endpoint_guidance_is_preserved_without_exposing_bare_codes(client, ab, fakes, monkeypatch, status, detail, expected):
    from fastapi import HTTPException
    from app.ai import executor
    _, tenant = ab
    def fail(*_args):
        raise HTTPException(status, detail=detail)
    monkeypatch.setitem(executor.HANDLERS, 'find_client', fail)
    fakes.answers.append(plan_of({'action': 'find_client', 'name': tenant['client']['full_name']},
                                 summary='Найду клиента'))
    draft = ask(client, tenant, 'Анастасию на завтра')
    assert draft.status_code == 200, draft.text
    result = client.post('/assistant/execute', json={'plan_id': draft.json()['plan_id']},
                         headers={**tenant['headers'], 'Accept-Language': 'ru'})
    assert result.status_code == 200, result.text
    error = result.json()['results'][0]['error']
    assert expected in error['message'], error
    assert error['message'] != error['code']


def test_layperson_dialogue_ends_in_one_booking_at_the_time_they_meant(client, db, ab, fakes):
    """Duty plan item 8: a name typed in the other alphabet, no time at first, then an answer the
    model turns into the wrong summer/winter offset. One visit, at the wall clock they said, and
    neither a repeated request nor a repeated confirmation books twice."""
    from app.db.models.client import Client
    from app.db.models.visit import Visit
    _, tenant = ab
    db.get(Client, tenant['client']['id']).full_name = 'Emily Clark'
    db.commit()
    chat = conversation(client, tenant)
    zone = ZoneInfo('Europe/Prague')
    tomorrow = (datetime.now(zone) + timedelta(days=1)).replace(hour=14, minute=0, second=0, microsecond=0)
    # the model mixes summer and winter time up: give it the offset Prague does NOT use that day
    wrong = '+01:00' if tomorrow.utcoffset() == timedelta(hours=2) else '+02:00'
    question = 'На какое время завтра записать Emily Clark на массаж?'
    fakes.answers.extend([
        plan_of(summary='Нужно время', questions=[question]),
        plan_of({'action': 'create_visit', 'client_id': tenant['client']['id'],
                 'service_id': tenant['service']['id'],
                 'start': tomorrow.strftime('%Y-%m-%dT%H:%M:%S') + wrong}, summary='Запись на завтра в 14:00'),
    ])
    before = db.query(Visit).count()
    first = ask(client, tenant, 'запиши эмили кларк на массаж', conversation_id=chat,
                client_request_id=str(uuid4()))
    assert first.status_code == 200, first.text
    assert first.json()['questions'] == [question] and first.json()['actions'] == []
    block = fakes.payloads[-1]['messages'][1]['content'].split('<data>', 1)[1].rsplit('</data>', 1)[0]
    assert tenant['client']['id'] in [c['id'] for c in json.loads(block)['context']['client_matches']]

    answer_id = str(uuid4())
    second = ask(client, tenant, 'завтра в 14', conversation_id=chat, client_request_id=answer_id)
    assert second.status_code == 200, second.text
    calls = len(fakes.payloads)
    again = ask(client, tenant, 'завтра в 14', conversation_id=chat, client_request_id=answer_id)
    assert again.status_code == 200, again.text
    assert again.json()['plan_id'] == second.json()['plan_id'] and len(fakes.payloads) == calls
    assert db.query(Visit).count() == before

    payload = {'plan_id': second.json()['plan_id']}
    done = client.post('/assistant/execute', json=payload, headers=tenant['headers'])
    assert done.status_code == 200 and done.json()['ok'], done.text
    created = db.get(Visit, done.json()['results'][0]['data']['visit_id'])
    assert created.date_start == tomorrow.astimezone(ZoneInfo('UTC')).replace(tzinfo=None)
    replay = client.post('/assistant/execute', json=payload, headers=tenant['headers'])
    assert replay.json() == done.json()
    assert db.query(Visit).count() == before + 1
