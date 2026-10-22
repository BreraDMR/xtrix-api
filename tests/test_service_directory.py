"""Service pages have a stable order within the allowed directory."""
from app.db.models.service import Service


def test_service_pages_are_ordered_and_never_include_another_specialist(client, db, make_user, headers_for):
    owner = make_user('specialist')
    other = make_user('specialist')
    rows = [Service(id=900000 + i, specialist_id=owner.id, name=f'Service {i:03d}',
                    duration_minutes=30, price=10, is_active=i % 2 == 0)
            for i in reversed(range(125))]
    db.add_all(rows + [Service(id=900125, specialist_id=other.id, name='Private service',
                              duration_minutes=30, price=10, is_active=True)])
    db.commit()
    headers = headers_for(owner)
    first = client.get('/services/', headers=headers, params={'per_page': 100}).json()
    second = client.get('/services/', headers=headers, params={'per_page': 100, 'page': 2}).json()
    assert (first['total'], first['pages'], second['total']) == (125, 2, 125)
    assert [row['id'] for row in first['items'] + second['items']] == list(range(900000, 900125))
    active = client.get('/services/', headers=headers, params={'per_page': 100, 'active_only': True}).json()
    assert active['total'] == 63
    assert all(row['is_active'] for row in active['items'])
