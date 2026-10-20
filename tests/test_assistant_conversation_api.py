"""Conversation APIs are private and retry-safe; no model or external service is needed."""
from uuid import uuid4

from app.db.models.assistant_conversation import AssistantConversation, AssistantMessage
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


def test_list_messages_stable_order_and_pagination(client, db, make_user, headers_for):
    owner = make_user()
    headers = headers_for(owner)
    chat = create(client, headers, title='Message chat').json()
    chat_id = chat['id']

    # Insert 5 messages with explicit ordinals
    for i in range(1, 6):
        db.add(AssistantMessage(
            conversation_id=chat_id,
            ordinal=i,
            request_id=uuid4(),
            request_hash='dummy',
            role='user' if i % 2 == 1 else 'assistant',
            text=f'Message {i}',
            status='succeeded',
        ))
    db.commit()

    # First page: limit 3 (most recent messages)
    resp = client.get(f'{URL}/{chat_id}/messages?limit=3', headers=headers)
    assert resp.status_code == 200, resp.text
    page1 = resp.json()
    assert len(page1['items']) == 3
    assert [m['ordinal'] for m in page1['items']] == [3, 4, 5]
    assert page1['next_before'] == 3

    # Next page: before=3
    resp2 = client.get(f'{URL}/{chat_id}/messages?limit=3&before=3', headers=headers)
    assert resp2.status_code == 200
    page2 = resp2.json()
    assert len(page2['items']) == 2
    assert [m['ordinal'] for m in page2['items']] == [1, 2]
    assert page2['next_before'] is None


def test_list_messages_stranger_cannot_access(client, make_user, headers_for):
    owner = make_user()
    stranger = make_user()
    chat = create(client, headers_for(owner), title='Secret chat').json()

    resp = client.get(f'{URL}/{chat["id"]}/messages', headers=headers_for(stranger))
    assert resp.status_code == 404
    assert resp.json()['detail']['code'] == 'conversation_not_found'


def test_rename_conversation(client, db, make_user, headers_for):
    owner = make_user()
    headers = headers_for(owner)
    chat = create(client, headers, title='Old Name').json()
    chat_id = chat['id']

    # Successful rename with whitespace stripping
    resp = client.patch(f'{URL}/{chat_id}', json={'title': '  New Clean Name  '}, headers=headers)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data['title'] == 'New Clean Name'
    assert data['id'] == chat_id

    # Empty or whitespace only title -> 422
    err_resp = client.patch(f'{URL}/{chat_id}', json={'title': '   '}, headers=headers)
    assert err_resp.status_code == 422

    # Stranger cannot rename -> 404
    stranger = make_user()
    stranger_resp = client.patch(f'{URL}/{chat_id}', json={'title': 'Hacked'}, headers=headers_for(stranger))
    assert stranger_resp.status_code == 404

    # Archived conversation cannot be renamed -> 404
    db.query(AssistantConversation).filter(AssistantConversation.id == chat_id).update({'archived_at': AssistantConversation.created_at})
    db.commit()
    archived_resp = client.patch(f'{URL}/{chat_id}', json={'title': 'Archived Title'}, headers=headers)
    assert archived_resp.status_code == 404


def test_archive_conversation_is_idempotent_and_isolated(client, db, make_user, headers_for):
    owner = make_user()
    headers = headers_for(owner)
    chat = create(client, headers, title='To Archive').json()
    chat_id = chat['id']

    # Add a message to verify it is not cascade deleted
    db.add(AssistantMessage(
        conversation_id=chat_id,
        ordinal=1,
        request_id=uuid4(),
        request_hash='dummy',
        role='user',
        text='Preserved message',
        status='succeeded',
    ))
    db.commit()

    # First archive call
    resp1 = client.post(f'{URL}/{chat_id}/archive', headers=headers)
    assert resp1.status_code == 200, resp1.text
    data1 = resp1.json()
    assert data1['archived_at'] is not None
    archived_at = data1['archived_at']

    # Repeated archive call (idempotent retry)
    resp2 = client.post(f'{URL}/{chat_id}/archive', headers=headers)
    assert resp2.status_code == 200
    data2 = resp2.json()
    assert data2['archived_at'] == archived_at

    # Excluded from active conversations list
    list_resp = client.get(URL, headers=headers).json()
    assert all(c['id'] != chat_id for c in list_resp['items'])

    # Messages are still intact in DB
    assert db.query(AssistantMessage).filter(AssistantMessage.conversation_id == chat_id).count() == 1

    # Stranger cannot archive -> 404
    stranger = make_user()
    stranger_resp = client.post(f'{URL}/{chat_id}/archive', headers=headers_for(stranger))
    assert stranger_resp.status_code == 404




