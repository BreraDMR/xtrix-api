"""Financial quote reads expose only the stored projection, never catalog guesses."""
from decimal import Decimal

import pytest
from sqlalchemy import event,update

from app.db.models.business import Business
from app.db.models.service import Service
from app.db.models.visit import Visit
from app.db.models.visit_item import VisitItem
from tests import test_completed_payment_guard as base
from tests import test_staff_permissions as staff

salon=base.salon
staff_salon=staff.salon


def url(visit_id):return f'/visits/{visit_id}/price'
def private(response,status):
    assert response.status_code==status,response.text
    assert response.headers['cache-control']=='no-store'


def test_read_returns_stored_quote_and_stable_fingerprint_without_writes(client,db,salon):
    visit_id,items=base.create_quote(client,salon);before=base.snapshot(db,visit_id)
    db.execute(update(Service).where(Service.business_id==salon['business']['id']).values(price=999,duration_minutes=15))
    db.execute(update(Business).where(Business.id==salon['business']['id']).values(currency='EUR'));db.commit()
    writes=[]
    def capture(conn,cursor,sql,*args):
        if sql.lstrip().upper().startswith(('UPDATE','INSERT','DELETE')):writes.append(sql)
    event.listen(db.get_bind(),'before_cursor_execute',capture)
    try:response=client.get(url(visit_id),headers=salon['headers'])
    finally:event.remove(db.get_bind(),'before_cursor_execute',capture)
    private(response,200);quote=response.json()
    assert not writes and quote['currency']=='CZK'
    assert Decimal(quote['total'])==Decimal(str(dict(before[0])['price']))
    assert [Decimal(item['price']) for item in quote['items']]==[dict(item)['price'] for item in before[1]]
    assert set(quote)=={'visit_id','currency','total','items','expected_quote'}
    assert quote['expected_quote'].startswith('vprice1_')
    assert client.get(url(visit_id),headers=salon['headers']).json()==quote


def test_unknown_currency_stays_unknown_even_with_current_known_salon_currency(client,db,salon):
    visit_id,_items=base.create_quote(client,salon)
    db.execute(update(Visit).where(Visit.id==visit_id).values(currency_snapshot=None));db.commit()
    response=client.get(url(visit_id),headers=salon['headers']);private(response,200)
    assert response.json()['currency'] is None


def test_item_price_change_invalidates_fingerprint_even_with_same_parent_total(client,db,salon):
    visit_id,_items=base.create_quote(client,salon);first=client.get(url(visit_id),headers=salon['headers']).json()
    item_id=first['items'][0]['item_id']
    db.execute(update(VisitItem).where(VisitItem.id==item_id).values(price=1));db.commit()
    second=client.get(url(visit_id),headers=salon['headers']);private(second,200)
    assert second.json()['total']==first['total']
    assert second.json()['expected_quote']!=first['expected_quote']


def test_staff_without_financial_permission_cannot_read_quote(client,staff_salon):
    w=staff_salon;staff._set_rights(client,w,staff.NO_RIGHTS)
    created=client.post('/visits/',headers=w['h'],json={**staff._visit_body(w),'price':12})
    assert created.status_code==200,created.text
    private(client.get(url(created.json()['id']),headers=w['eh']),403)


def test_foreign_business_and_missing_credentials_do_not_expose_quote(client,db,salon,make_user,headers_for):
    visit_id,_items=base.create_quote(client,salon)
    outsider=make_user('business_owner',email='outside-price@example.com')
    result=client.post('/businesses/',headers=headers_for(outsider),json={'name':'Other quote salon','business_type':'salon'})
    assert result.status_code==200,result.text
    private(client.get(url(visit_id),headers=headers_for(outsider)),404)
    private(client.get(url(visit_id)),401)
