from datetime import timedelta
from tests.test_waitlist import _join, _visit, _cancel
pytest_plugins = ['tests.test_waitlist']
from app.db.models.waitlist import WaitlistEntry
from app.api.crud.crud_waitlist import dedupe_key
from app.api.core import waitlist_service
from app.api.core.clock import utcnow


def test_long_name_does_not_crash_db(client, salon):
    response = _join(client, salon, name='A' * 201)
    assert response.status_code in (202, 422), response.text


def test_open_ended_preferences_do_not_dedupe_to_one_day(client, db, salon):
    first = _join(client, salon, preferred_date_to=salon['local_day'].isoformat())
    assert first.status_code == 202
    second = _join(client, salon, preferred_date_to=None)
    assert second.status_code == 202
    assert second.json()['created'] is True, second.text


def test_matching_scans_beyond_first_hundred_nonmatches(client, db, salon):
    day = salon['local_day']
    for n in range(101):
        preference = 'evening' if n < 100 else 'morning'
        email = f'queue{n}@example.com'
        db.add(WaitlistEntry(
            business_id=salon['business']['id'], service_id=salon['service']['id'],
            specialist_id=salon['owner'].id, full_name=f'Queue {n}', email=email,
            preferred_date_from=day, preferred_date_to=day, time_preference=preference,
            processing_consent=True, status='active',
            dedupe_key=dedupe_key(salon['business']['id'],email,salon['service']['id'],salon['owner'].id,day,day,preference),
        ))
    db.commit()
    offers = waitlist_service.release_slot(db, db.get(__import__('app.db.models.business',fromlist=['Business']).Business,salon['business']['id']), salon['service']['id'],salon['owner'].id,salon['start'])
    assert len(offers) == 1, f'Expected morning entry101 offered, got {len(offers)}'


def test_acceptance_preserves_waitlisted_phone_for_new_client(client, db, salon):
    from tests.test_waitlist import _offer_token, _headers
    from app.db.models.client import Client
    from app.db.models.visit import Visit
    phone = '+420777222333'
    joined = _join(client, salon, email='newphone@example.com',phone=phone)
    assert joined.status_code == 202, joined.text
    visit = _visit(client, salon)
    assert _cancel(client, salon, visit['id']).status_code == 200
    token = _offer_token(db,salon['business']['id'],'newphone@example.com')
    accepted = client.post('/public/waitlist/offer/accept',headers=_headers(token))
    assert accepted.status_code == 200, accepted.text
    db.expire_all()
    current = db.query(Visit).filter(Visit.status!='cancelled').one()
    person = db.get(Client,current.client_id)
    assert person.contact_phone == phone, f'Waitlist phone was lost: {person.contact_phone!r}'
