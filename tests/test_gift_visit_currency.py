"""External regression probe: a money gift cannot invent a visit's price currency."""
import pytest
from sqlalchemy import select

from app.api.core import gift_service
from app.db.models.business import Business
from app.db.models.gift import GiftLedgerEntry, GiftOffer
from app.db.models.visit import Visit
from app.db.models.visit_item import VisitItem
from tests import test_gifts as gifts


gift_world = gifts.gift_world


@pytest.mark.parametrize('visit_currency', ['EUR', None])
def test_money_gift_requires_the_same_known_visit_currency(client, db, gift_world, visit_currency):
    business_id = gift_world['business']['id']
    db.get(Business, business_id).currency = 'CZK'
    db.get(GiftOffer, gift_world['money_offer']['id']).currency = 'CZK'
    db.commit()
    _, paid = gifts._pay(client, gift_world, request_key='unit-probe-purchase')
    certificate = gifts._certificate(db, paid)
    assert certificate.currency == 'CZK'
    certificate_id, code = certificate.id, certificate.code
    visit_id = gift_world['visit']['id']
    visit = db.get(Visit, visit_id)
    visit.price = 40
    visit.currency_snapshot = visit_currency
    db.query(VisitItem).filter(VisitItem.visit_id == visit_id).update({'price': 40})
    db.get(Business, business_id).currency = 'EUR'
    db.commit()
    before = [dict(row) for row in db.execute(select(GiftLedgerEntry.__table__).where(
        GiftLedgerEntry.certificate_id == certificate_id)).mappings()]
    error = None
    try:
        gift_service.redeem(db, business_id, code, visit_id, 40, 'Synthetic currency probe',
                            'unit-probe-redemption', actor_user_id=gift_world['owner'].id)
    except gift_service.GiftError as exc:
        error = exc
        db.rollback()
    after = [dict(row) for row in db.execute(select(GiftLedgerEntry.__table__).where(
        GiftLedgerEntry.certificate_id == certificate_id)).mappings()]
    new_rows = [row for row in after if row['id'] not in {item['id'] for item in before}]
    assert error is not None, (
        f'Expected GiftError before redemption: certificate=CZK, visit={visit_currency!r}; '
        f'actual committed ledger rows={[(row["entry_type"], str(row["amount_delta"]), row["visit_id"]) for row in new_rows]}'
    )
    assert after == before


def test_same_stored_currency_works_after_salon_currency_change_and_replays(client,db,gift_world):
    world=gift_world;business_id=world['business']['id']
    db.get(Business,business_id).currency='CZK';db.get(GiftOffer,world['money_offer']['id']).currency='CZK';db.commit()
    _,paid=gifts._pay(client,world,request_key='same-unit-purchase');code=gifts._certificate(db,paid).code
    visit=db.get(Visit,world['visit']['id']);visit.price=40;visit.currency_snapshot='CZK'
    db.query(VisitItem).filter_by(visit_id=visit.id).update({'price':40});db.get(Business,business_id).currency='EUR';db.commit()
    cert,first,*_=gift_service.redeem(db,business_id,code,visit.id,20,None,'same-unit-redeem',world['owner'].id)
    first_id=first.id;visit_id=visit.id
    # Replay returns the original receipt even if a legacy writer altered the unit.
    db.get(Visit,visit_id).currency_snapshot='EUR';db.commit()
    _,again,*_=gift_service.redeem(db,business_id,code,visit_id,20,None,'same-unit-redeem',world['owner'].id)
    assert again.id==first_id
    assert db.query(GiftLedgerEntry).filter_by(visit_id=visit_id,entry_type='redeemed').count()==1
    with pytest.raises(gift_service.GiftError) as exc:
        gift_service.redeem(db,business_id,code,visit_id,10,None,'new-unit-redeem',world['owner'].id)
    assert exc.value.reason=='gift_currency_mismatch';db.rollback()


@pytest.mark.parametrize('unit',['EUR',None])
def test_package_units_do_not_require_money_currency(client,db,gift_world,unit):
    w=gift_world;_,paid=gifts._pay(client,w,offer_id=w['package_offer']['id'],request_key='package-unit-purchase')
    code=gifts._certificate(db,paid).code;visit_id=w['visit']['id']
    db.get(Visit,visit_id).currency_snapshot=unit;db.commit()
    _,entry,amount,units=gift_service.redeem(db,w['business']['id'],code,visit_id,None,None,'package-unit-redeem',w['owner'].id)
    assert units==1 and amount==0 and entry.units_delta==-1


@pytest.mark.parametrize('locale',['en','ru','cz'])
def test_http_unknown_unit_returns_localized_refusal_without_spending(client,db,gift_world,locale):
    from app.api.core.i18n import _ as translate
    w=gift_world;_,paid=gifts._pay(client,w,request_key='http-unit-purchase')
    visit_id=w['visit']['id'];db.get(Visit,visit_id).currency_snapshot=None;db.commit()
    before=db.query(GiftLedgerEntry).count()
    response=client.post('/businesses/me/gift-certificates/redeem',headers={**w['headers'],'Accept-Language':locale},
        json={'code':paid['certificate']['code'],'visit_id':visit_id,'amount':10,'request_key':'http-unit-redeem'})
    assert response.status_code==409,response.text
    assert response.json()['error']['message']==translate('gift_currency_mismatch',locale)
    assert db.query(GiftLedgerEntry).count()==before


def test_new_matching_certificate_cannot_mix_with_old_foreign_currency_ledger(client,db,gift_world):
    w=gift_world;business_id=w['business']['id'];visit_id=w['visit']['id']
    db.get(Business,business_id).currency='CZK';db.get(GiftOffer,w['money_offer']['id']).currency='CZK'
    db.get(Visit,visit_id).currency_snapshot='CZK';db.get(Visit,visit_id).price=40
    db.query(VisitItem).filter_by(visit_id=visit_id).update({'price':40});db.commit()
    _,paid=gifts._pay(client,w,request_key='first-currency-certificate');old_code=gifts._certificate(db,paid).code
    gift_service.redeem(db,business_id,old_code,visit_id,10,None,'old-currency-redeem',w['owner'].id)
    db.get(Visit,visit_id).currency_snapshot='EUR';db.get(Business,business_id).currency='EUR'
    db.get(GiftOffer,w['money_offer']['id']).currency='EUR';db.commit()
    _,second=gifts._pay(client,w,request_key='second-currency-certificate');new_code=gifts._certificate(db,second).code
    before=db.query(GiftLedgerEntry).count()
    with pytest.raises(gift_service.GiftError) as exc:
        gift_service.redeem(db,business_id,new_code,visit_id,10,None,'mixed-history-redeem',w['owner'].id)
    assert exc.value.reason=='gift_currency_mismatch';db.rollback()
    assert db.query(GiftLedgerEntry).count()==before
