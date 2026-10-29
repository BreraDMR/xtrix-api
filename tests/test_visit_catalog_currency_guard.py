"""Changing a catalog chain must not stamp today's price with an old known unit."""
from datetime import timedelta

import pytest
from sqlalchemy import select,update
from sqlalchemy.orm import Session

from app.api.core import visit_settlement_guard
from app.api.core.i18n import _
from app.api.crud import crud_visit
from app.db.models.business import Business
from app.db.models.business_audit_event import BusinessAuditEvent
from app.db.models.service import Service
from app.db.models.visit import Visit
from app.db.models.visit_item import VisitItem
from app.schemas.visit import VisitUpdate
from tests import test_visit_financial_privacy as privacy

salon=privacy.salon


@pytest.fixture
def world(client,db,salon):
    w=salon
    db.get(Business,w['business']['id']).currency='CZK'
    db.get(Service,w['services'][0]['id']).price=1000;db.commit()
    created=privacy.ok(client.post('/visits/',headers=w['oh'],json={**w['body'],'price':1000,'payment_method':None}))
    w['visit_id']=created['id']
    assert db.get(Visit,w['visit_id']).price==1000 and db.get(Visit,w['visit_id']).currency_snapshot=='CZK'
    db.get(Business,w['business']['id']).currency='EUR'
    db.get(Service,w['services'][0]['id']).price=40
    db.get(Service,w['services'][1]['id']).price=40;db.commit()
    return w


def state(db,w):
    return (dict(db.execute(select(Visit.__table__).where(Visit.id==w['visit_id'])).mappings().one()),
            [dict(row) for row in db.execute(select(VisitItem.__table__).where(VisitItem.visit_id==w['visit_id']).order_by(VisitItem.id)).mappings()],
            db.query(BusinessAuditEvent).count())


def payload(w,shape):
    if shape=='service':return {'service_id':w['services'][1]['id']}
    if shape=='items':return {'items':[{'service_id':w['services'][1]['id'],'specialist_id':w['staff'].id}]}
    return {'items':[{'service_id':w['services'][0]['id'],'specialist_id':w['owner'].id}]}


@pytest.mark.parametrize('shape',['service','items','master'])
def test_new_catalog_chain_needs_an_explicit_price_currency_change(client,db,world,shape):
    w=world;before=state(db,w);db.rollback()
    for language in ['en','ru','cz']:
        response=client.put(f"/visits/{w['visit_id']}",headers={**w['oh'],'Accept-Language':language},json=payload(w,shape))
        stored=db.execute(select(Visit.price,Visit.currency_snapshot).where(Visit.id==w['visit_id'])).one()
        assert response.status_code==409, f'Expected explicit-currency refusal, got HTTP{response.status_code}, stored={tuple(stored)}, body={response.text}'
        assert response.json()['error']['detail']['code']=='visit_price_currency_explicit_required'
        assert response.json()['error']['message']==_('visit_price_currency_explicit_required',language)
        assert state(db,w)==before;db.rollback()


def test_same_chain_move_note_status_and_scalar_price_keep_the_saved_unit(client,db,world):
    w=world;visit=db.get(Visit,w['visit_id']);new_start=visit.date_start+timedelta(days=1)
    for body in [{'date_start':new_start.isoformat()}, {'note':'Same sold quote'},
                 {'items':[{'service_id':w['services'][0]['id'],'specialist_id':w['staff'].id}]}, {'price':800}]:
        response=client.put(f"/visits/{w['visit_id']}",headers=w['oh'],json=body)
        assert response.status_code==200,response.text
    db.expire_all();visit=db.get(Visit,w['visit_id'])
    assert visit.currency_snapshot=='CZK' and visit.price==800 and visit.note=='Same sold quote'
    response=client.put(f"/visits/{w['visit_id']}",headers=w['oh'],json={'status':'cancelled'})
    assert response.status_code==200,response.text
    assert db.execute(select(Visit.currency_snapshot).where(Visit.id==w['visit_id'])).scalar_one()=='CZK'


def test_unknown_unit_is_preserved_without_guessing_the_current_business_currency(client,db,world):
    w=world;db.execute(update(Visit).where(Visit.id==w['visit_id']).values(currency_snapshot=None));db.commit()
    response=client.put(f"/visits/{w['visit_id']}",headers=w['oh'],json=payload(w,'service'))
    assert response.status_code==200,response.text
    row=db.execute(select(Visit.price,Visit.currency_snapshot).where(Visit.id==w['visit_id'])).one()
    assert tuple(row)==(40,None)


def test_matching_known_currency_allows_catalog_chain_change(client,db,world):
    w=world;db.execute(update(Visit).where(Visit.id==w['visit_id']).values(currency_snapshot='EUR'));db.commit()
    response=client.put(f"/visits/{w['visit_id']}",headers=w['oh'],json=payload(w,'service'))
    assert response.status_code==200,response.text
    assert tuple(db.execute(select(Visit.price,Visit.currency_snapshot).where(Visit.id==w['visit_id'])).one())==(40,'EUR')


def test_stale_business_currency_cannot_bypass_the_guard(db,world):
    w=world;db.get(Business,w['business']['id']).currency='CZK';db.commit()
    cached=db.get(Business,w['business']['id']);assert cached.currency=='CZK'
    with Session(db.get_bind()) as writer:
        writer.execute(update(Business).where(Business.id==cached.id).values(currency='EUR'));writer.commit()
    assert cached.currency=='CZK';before=state(db,w)
    assert cached.currency=='CZK'
    with pytest.raises(visit_settlement_guard.VisitPriceChangeBlocked) as exc:
        crud_visit.update_visit(db,db.get(Visit,w['visit_id']),VisitUpdate(**payload(w,'service')),actor=w['owner'])
    assert exc.value.reason=='visit_price_currency_explicit_required'
    assert state(db,w)==before


def test_explicit_scalar_price_does_not_turn_a_changed_service_into_a_currency_override(client,db,world):
    w=world;before=state(db,w);db.rollback()
    response=client.put(f"/visits/{w['visit_id']}",headers=w['oh'],json={**payload(w,'service'),'price':40})
    assert response.status_code==409,response.text
    assert response.json()['error']['detail']['code']=='visit_price_currency_explicit_required'
    assert state(db,w)==before


def test_known_legacy_solo_unit_does_not_require_a_missing_business_currency(client, db, make_user, headers_for):
    from app.api.core.clock import utcnow

    solo = make_user('specialist')
    headers = headers_for(solo)
    customer = privacy.ok(client.post('/clients/', headers=headers, json={'full_name': 'Solo Client'}))
    services = [privacy.ok(client.post('/services/', headers=headers, json={
        'name': f'Solo Service {index}', 'price': price, 'duration_minutes': 30,
        'specialist_id': solo.id,
    })) for index, price in enumerate((1000, 40))]
    created = privacy.ok(client.post('/visits/', headers=headers, json={
        'client_id': customer['id'], 'specialist_id': solo.id,
        'date_start': (utcnow() + timedelta(days=2)).isoformat(),
        'service_id': services[0]['id'], 'price': 1000,
        'status': 'planned', 'payment_method': None,
    }))
    visit_id = created['id']
    assert db.get(Visit, visit_id).business_id is None
    # A stored legacy unit is not proof that a solo catalog has a current business unit.
    db.execute(update(Visit).where(Visit.id == visit_id).values(currency_snapshot='CZK'))
    db.commit()
    response = client.put(f'/visits/{visit_id}', headers=headers, json={'service_id': services[1]['id']})
    assert response.status_code == 200, response.text
    row = db.execute(select(Visit.price, Visit.currency_snapshot, Visit.business_id).where(Visit.id == visit_id)).one()
    assert tuple(row) == (40, 'CZK', None)
