from app.api.core.clock import utcnow
from app.api.crud import crud_user
from app.db.models.user import User
from tests.test_public_lists import _owner_with_business


def _read_pages(client, path, headers, expected_ids, **filters):
    seen = []
    for page in (1, 2, 3):
        response = client.get(path, headers=headers, params={**filters, 'page': page, 'per_page': 100})
        assert response.status_code == 200, response.text
        data = response.json()
        assert data['total'] == len(expected_ids)
        assert data['pages'] == 2
        seen.extend(row['id'] for row in data['items'])
    assert seen == expected_ids
    assert len(seen) == len(set(seen))


def test_business_users_have_stable_order_across_pages(client, make_user, headers_for, db):
    owner, headers, business_id = _owner_with_business(client, make_user, headers_for, 'Alpha')
    _, _, other_id = _owner_with_business(client, make_user, headers_for, 'Beta')
    common = dict(full_name='Specialist', hashed_password='', role='specialist',
                  business_id=business_id, moderation_status='approved')
    db.add_all([User(id=i, **common) for i in reversed(range(1000, 1125))])
    db.add_all([
        User(id=2000, **{**common, 'business_id': other_id}),
        User(id=2001, **{**common, 'deleted_at': utcnow()}),
        User(id=2002, **{**common, 'role': 'employee'}),
    ])
    db.commit()
    expected = list(range(1000, 1125))
    _read_pages(client, '/users/', headers, expected, role='specialist')
    users, total = crud_user.list_users(db, role='specialist', viewer=owner)
    assert total == 125
    assert [user.id for user in users] == expected


def test_pending_moderation_has_stable_order_across_pages(client, make_user, headers_for, db):
    admin = make_user('admin')
    owner = make_user('business_owner')
    common = dict(full_name='Pending', hashed_password='', role='specialist', moderation_status='pending')
    db.add_all([User(id=i, **common) for i in reversed(range(1000, 1125))])
    db.add_all([
        User(id=2000, **{**common, 'moderation_status': 'approved'}),
        User(id=2001, **{**common, 'deleted_at': utcnow()}),
    ])
    db.commit()
    expected = list(range(1000, 1125))
    assert client.get('/users/pending', headers=headers_for(owner)).status_code == 403
    _read_pages(client, '/users/pending', headers_for(admin), expected)
    users, total = crud_user.list_users_pending_moderation(db)
    assert total == 125
    assert [user.id for user in users] == expected
