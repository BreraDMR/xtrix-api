"""Conversation APIs are private and retry-safe; no model or external service is needed."""
from uuid import uuid4

from app.db.models.assistant_conversation import AssistantConversation
from app.db.models.business import Business

URL = '/assistant/conversations'


def create(client, headers, **fields):
    return client.post(URL, json={'client_request_id': str(uuid4()), **fields}, headers=headers)


def test_create_is_private_and_idempotent(client, db, make_user, headers_for):
    owner = make_user()
    key = str(uuid4())
    headers = headers_for(owner)
    first = create(client, headers, client_request_id=key, title='  My chat  ')
    assert first.status_code == 201, first.text
    body = first.json()
    assert body['title'] == 'My chat'
    assert body['created_at'].endswith('Z')
    assert 'owner_user_id' not in body and 'business_id' not in body
    again = create(client, headers, client_request_id=key, title='My chat')
    assert again.status_code == 201
    assert again.json() == body
    assert db.query(AssistantConversation).count() == 1
    changed = create(client, headers, client_request_id=key, title='Changed payload')
    assert changed.status_code == 409
    assert db.query(AssistantConversation).count() == 1


def test_default_title_uses_the_requested_language(client, make_user, headers_for):
    headers = {**headers_for(make_user()), 'Accept-Language': 'ru'}
    result = create(client, headers)
    assert result.status_code == 201
    assert result.json()['title'] == 'Новый чат'


def test_guest_and_client_role_cannot_create_chats(client, db, make_user, headers_for):
    assert create(client, {}).status_code == 401
    assert create(client, headers_for(make_user('client'))).status_code == 403
    assert db.query(AssistantConversation).count() == 0


def test_request_key_is_private_and_cannot_cross_business_scope(client, db, make_user, headers_for):
    owner, stranger = make_user(), make_user()
    key = str(uuid4())
    first = create(client, headers_for(owner), client_request_id=key)
    second = create(client, headers_for(stranger), client_request_id=key)
    assert first.status_code == second.status_code == 201
    assert first.json()['id'] != second.json()['id']
    business = Business(name='Another scope', business_type='salon')
    db.add(business)
    db.commit()
    owner.business_id = business.id
    db.commit()
    conflict = create(client, headers_for(owner), client_request_id=key)
    assert conflict.status_code == 409
    assert first.json()['id'] not in conflict.text
    assert db.query(AssistantConversation).count() == 2


def test_create_cannot_accept_owner_or_business_from_browser(client, db, make_user, headers_for):
    owner = make_user()
    for name in ['owner_user_id', 'business_id']:
        result = create(client, headers_for(owner), **{name: 123})
        assert result.status_code == 422
    assert db.query(AssistantConversation).count() == 0


def test_concurrent_create_with_one_key_returns_one_chat(client, db, make_user, headers_for):
    from concurrent.futures import ThreadPoolExecutor
    owner = make_user()
    headers = headers_for(owner)
    key = str(uuid4())
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: create(client, headers, client_request_id=key), range(2)))
    assert [response.status_code for response in responses] == [201, 201]
    assert responses[0].json() == responses[1].json()
    assert db.query(AssistantConversation).count() == 1


def test_list_conversations_excludes_archived_and_other_users(client, db, make_user, headers_for):
    owner = make_user()
    other_user = make_user()
    c1 = create(client, headers_for(owner), title='Chat 1').json()
    c2 = create(client, headers_for(owner), title='Chat 2').json()
    create(client, headers_for(other_user), title='Stranger Chat')

    # Archive c1 directly in DB to simulate archived conversation
    db.query(AssistantConversation).filter(AssistantConversation.id == c1['id']).update({'archived_at': AssistantConversation.created_at})
    db.commit()

    resp = client.get(URL, headers=headers_for(owner))
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data['total'] == 1
    assert len(data['items']) == 1
    assert data['items'][0]['id'] == c2['id']
    assert data['items'][0]['title'] == 'Chat 2'


def test_list_conversations_pagination(client, make_user, headers_for):
    owner = make_user()
    headers = headers_for(owner)
    for i in range(5):
        create(client, headers, title=f'Chat {i}')

    resp = client.get(f'{URL}?limit=2&offset=0', headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data['total'] == 5
    assert len(data['items']) == 2
    assert data['limit'] == 2
    assert data['offset'] == 0

