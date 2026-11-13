"""A plan that was shown but never confirmed, and then the person changes their mind.

"Book Elena at 12" -> "no, make it 14": the first plan never ran, so the next turn has to rebuild it
with the new time - not look for a visit to move. For that the model needs the steps of that plan,
and the old card must not stay runnable next to the new one.
"""
import json
from uuid import uuid4

from tests import test_assistant_api as api

ab = api.ab
fakes = api.fakes


def history(fakes):
    block = fakes.payloads[-1]['messages'][1]['content'].split('<data>', 1)[1].rsplit('</data>', 1)[0]
    return json.loads(block)['context']['history']


def booking(tenant, start='2027-01-10T12:00:00+01:00'):
    return {'action': 'create_visit', 'client_id': tenant['client']['id'], 'service_id': tenant['service']['id'],
            'specialist_id': tenant['owner'].id, 'start': start}


def test_steps_of_an_unconfirmed_plan_reach_the_next_turn(client, ab, fakes):
    _, tenant = ab
    chat = api.conversation(client, tenant)
    fakes.answers.append(api.plan_of(booking(tenant), summary='Запишу на 12:00'))
    first = api.ask(client, tenant, 'Запиши на стрижку в 12', conversation_id=chat, client_request_id=str(uuid4()))
    assert first.status_code == 200, first.text
    second = api.ask(client, tenant, 'ой нет, лучше в 14', conversation_id=chat, client_request_id=str(uuid4()))
    assert second.status_code == 200, second.text
    item = next(item for item in history(fakes) if item['role'] == 'assistant')
    assert item['execution'] == {'status': 'not_started'}
    [step] = item['plan']
    assert step['action'] == 'create_visit'
    assert step['client_id'] == tenant['client']['id'] and step['client_name'] == tenant['client']['full_name']
    assert step['service_id'] == tenant['service']['id'] and step['service_name'] == tenant['service']['name']
    assert step['specialist_id'] == tenant['owner'].id
    assert step['start'] == '2027-01-10T12:00:00+01:00'  # the wall clock the person saw


def test_a_plan_that_ran_does_not_repeat_its_steps(client, ab, fakes):
    _, tenant = ab
    chat = api.conversation(client, tenant)
    fakes.answers.append(api.plan_of(booking(tenant), summary='Запишу'))
    draft = api.ask(client, tenant, 'Запиши в 12', conversation_id=chat, client_request_id=str(uuid4()))
    assert api.run(client, tenant, draft.json()['plan_id']).json()['ok'] is True
    api.ask(client, tenant, 'спасибо', conversation_id=chat, client_request_id=str(uuid4()))
    item = next(item for item in history(fakes) if item['role'] == 'assistant')
    assert item['execution']['status'] == 'succeeded' and 'plan' not in item


def test_the_prompt_says_how_to_handle_a_correction():
    from app.ai.prompt import system_prompt
    text = system_prompt()
    assert 'not_started' in text and 'reschedule_visit' in text and 'plan' in text
    assert 'future tense' in text
