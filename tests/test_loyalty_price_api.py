"""The price after bonuses uses immutable receipts, never today's point rate."""
from decimal import Decimal

import pytest

from app.api.core import loyalty_logic
from app.db.models.business import Business
from app.db.models.loyalty_program import LoyaltyProgram,LoyaltyTransaction
from app.db.models.user import User
from tests import test_loyalty_visit_quote_lock as quote


@pytest.fixture
def world(db,monkeypatch):
    return quote.world.__wrapped__(db,monkeypatch)


def test_price_after_bonus_uses_the_recorded_discount_after_rate_and_business_currency_change(client,db,world,headers_for):
    assert loyalty_logic.use_points_for_visit(db,world['client'],world['business'],5,3,visit_id=world['visit'])[0]
    db.get(LoyaltyProgram,world['program']).currency_per_point=100
    db.get(Business,world['business']).currency='CZK';db.commit()
    response=client.get(f"/visits/{world['visit']}/loyalty-price",headers=headers_for(db.get(User,world['staff'])))
    assert response.status_code==200,response.text
    assert response.headers['cache-control']=='no-store'
    body=response.json()
    assert body['currency']=='EUR' and body['quoted_total']=='999.00'
    assert body['loyalty_discount']=='8.00' and body['price_after_loyalty']=='991.00'
    assert body['requires_review'] is False and body['message'] is None
    assert 'remaining_amount' not in body


def test_unknown_historical_point_conversion_has_no_invented_money_total(client,db,world,headers_for):
    db.add(LoyaltyTransaction(client_id=world['client'],business_id=world['business'],visit_id=world['visit'],
        transaction_type='spent',points_amount=-5,points_balance_after=95,cashback_amount=0,cashback_balance_after=0));db.commit()
    response=client.get(f"/visits/{world['visit']}/loyalty-price",headers={**headers_for(db.get(User,world['staff'])),'Accept-Language':'ru'})
    assert response.status_code==200,response.text
    body=response.json();assert body['requires_review'] is True
    assert body['loyalty_discount'] is None and body['price_after_loyalty'] is None
    assert 'провер' in body['message'].lower()


@pytest.mark.parametrize('case',['foreign_client','different_unit','unknown_unit','overallocated'])
def test_unsafe_financial_history_never_invents_a_payable_price(client,db,world,headers_for,case):
    from app.db.models.visit import Visit
    row=LoyaltyTransaction(client_id=world['other_client'] if case=='foreign_client' else world['client'],
        business_id=world['business'],visit_id=world['visit'],transaction_type='spent',points_amount=-1,
        points_balance_after=99,cashback_amount=0,cashback_balance_after=0,
        discount_currency='CZK' if case=='different_unit' else 'EUR',
        discount_amount_exact=1000 if case=='overallocated' else 5)
    db.add(row)
    if case=='unknown_unit':db.get(Visit,world['visit']).currency_snapshot=None
    db.commit()
    response=client.get(f"/visits/{world['visit']}/loyalty-price",headers=headers_for(db.get(User,world['staff'])))
    assert response.status_code==200,response.text
    body=response.json();assert body['requires_review'] and body['price_after_loyalty'] is None
    assert body['loyalty_discount']==('1000.00' if case=='overallocated' else None)
    assert body['message']


def test_zero_quote_is_a_valid_zero_price_after_bonuses(client,db,world,headers_for):
    from app.db.models.visit import Visit
    db.get(Visit,world['visit']).price=0;db.commit()
    response=client.get(f"/visits/{world['visit']}/loyalty-price",headers=headers_for(db.get(User,world['staff'])))
    assert response.status_code==200,response.text
    assert response.json()['price_after_loyalty']=='0.00' and not response.json()['requires_review']


def test_projection_is_one_fresh_read_without_flushing_or_replaying_a_rate(db,world):
    from decimal import localcontext
    from sqlalchemy import event,update
    from sqlalchemy.orm import Session
    from app.api.core.loyalty_price_projection import read_loyalty_price
    from app.db.models.client import Client
    from app.db.models.visit import Visit
    from app.db.models.visit_item import VisitItem
    visit=db.get(Visit,world['visit']);assert visit.price==999
    with Session(db.get_bind()) as writer:
        writer.execute(update(Visit).where(Visit.id==visit.id).values(price=100))
        writer.execute(update(VisitItem).where(VisitItem.visit_id==visit.id).values(price=100));writer.commit()
    assert visit.price==999
    pending=Client(full_name='Unrelated pending client');db.add(pending);statements=[]
    def capture(conn,cursor,statement,*args):statements.append(statement)
    event.listen(db.get_bind(),'before_cursor_execute',capture)
    try:
        with localcontext() as context:
            context.prec=2
            result=read_loyalty_price(db,visit)
    finally:event.remove(db.get_bind(),'before_cursor_execute',capture)
    assert result.quoted_total==result.price_after_loyalty==Decimal('100.00')
    assert len(statements)==1 and statements[0].lstrip().upper().startswith('SELECT')
    assert 'loyalty_programs' not in statements[0] and pending.id is None
    assert visit.price==999
    db.rollback()


def test_retargeted_authorization_hint_cannot_read_another_client_quote(db,world):
    from sqlalchemy import update
    from sqlalchemy.orm import Session
    from app.api.core.loyalty_price_projection import read_loyalty_price
    from app.db.models.visit import Visit
    visit=db.get(Visit,world['visit'])
    with Session(db.get_bind()) as writer:
        writer.execute(update(Visit).where(Visit.id==visit.id).values(client_id=world['other_client']));writer.commit()
    assert visit.client_id==world['client']
    assert read_loyalty_price(db,visit) is None


def test_read_requires_financial_rights_and_keeps_all_failures_private(client,db,world,headers_for,make_user):
    from app.db.models.employee import Employee
    employee=make_user('employee',business_id=world['business'],employer_id=world['staff'])
    db.add(Employee(business_owner_id=world['staff'],employee_user_id=employee.id,
        permissions={'can_view_financials':False},is_active=True));db.commit()
    requests=[(f"/visits/{world['visit']}/loyalty-price",{},401),
        (f"/visits/{world['visit']}/loyalty-price",headers_for(employee),403),
        ('/visits/9999999/loyalty-price',headers_for(db.get(User,world['staff'])),404),
        ('/visits/0/loyalty-price',headers_for(db.get(User,world['staff'])),422)]
    for url,headers,status in requests:
        response=client.get(url,headers=headers)
        assert response.status_code==status,response.text
        assert response.headers['cache-control']=='no-store'
