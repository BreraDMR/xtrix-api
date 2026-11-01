"""A package unit covers a stored quote, without becoming gift money or cash."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from decimal import Decimal, localcontext, Inexact, Rounded
from threading import Barrier

import pytest
from sqlalchemy import text, update
from sqlalchemy.exc import DBAPIError
from alembic import command
from alembic.config import Config
from app.db.session import SessionLocal
from app.db.models.service import Service

from app.api.core import gift_service as gifts
from app.api.core.clock import utcnow
from app.db.models.business import Business
from app.db.models.client import Client
from app.db.models.gift import GiftCertificate, GiftOffer, GiftOrder, GiftLedgerEntry
from app.db.models.visit import Visit
from app.db.models.visit_item import VisitItem


@pytest.fixture
def package(db,make_user):
    owner=make_user('business_owner');business=Business(name='Package',business_type='salon',currency='EUR')
    client=Client(full_name='Package client');db.add_all([business,client]);db.flush();owner.business_id=business.id
    visit=Visit(business_id=business.id,client_id=client.id,specialist_id=owner.id,
        date_start=utcnow()+timedelta(days=1),status='planned',price=30.01,currency_snapshot='CZK')
    offer=GiftOffer(business_id=business.id,name='Package',kind='package',price=250,currency='EUR',visits_count=3,validity_days=365)
    db.add_all([visit,offer]);db.flush()
    order=GiftOrder(business_id=business.id,offer_id=offer.id,provider='sandbox',merchant='local',request_key='purchase',
        request_hash='0'*64,public_ref='package-order',public_token_hash='0'*64,status='paid',offer_snapshot={},
        amount=250,currency='EUR',buyer_name='Buyer',buyer_email='buyer@example.com',expires_at=utcnow()+timedelta(days=1))
    db.add(order);db.flush()
    cert=GiftCertificate(business_id=business.id,offer_id=offer.id,order_id=order.id,code='PACKAGE',
        code_hash=gifts.code_digest('PACKAGE'),code_last4='KAGE',kind='package',status='active',issuer_name='Salon',
        offer_name='Package',currency='EUR',face_value=0,visits_total=3,recipient_name='Recipient',buyer_name='Buyer',
        buyer_email='buyer@example.com',expires_at=utcnow()+timedelta(days=365))
    db.add(cert);db.flush();db.add(GiftLedgerEntry(certificate_id=cert.id,business_id=business.id,entry_type='issued',
        amount_delta=0,units_delta=3,request_key='issued'));db.commit()
    return {'business':business.id,'visit':visit.id,'certificate':cert.id,'owner':owner.id,'code':'PACKAGE'}


def redeem(db,p,key='redeem-1'):
    return gifts.redeem(db,p['business'],p['code'],p['visit'],None,None,key,p['owner'])


def reverse(db,p,entry,key='reverse-1'):
    return gifts.reverse(db,p['business'],p['certificate'],entry.id,key,None,p['owner'])


def test_new_package_saves_full_visit_price_in_visit_unit(db,package):
    cert,entry,money,units=redeem(db,package)
    assert entry.coverage_currency=='CZK' and entry.coverage_amount_exact==Decimal('30.01')
    assert entry.amount_delta==money==0 and entry.units_delta==-1 and units==1
    assert gifts._totals(db,cert.id)==(Decimal('0.00'),2)


def test_database_rejects_rounded_coverage_for_a_subcent_stored_quote(db, package):
    db.execute(update(Visit).where(Visit.id == package['visit']).values(price=1.0000000000000002))
    db.commit()
    entry = GiftLedgerEntry(certificate_id=package['certificate'], business_id=package['business'],
        visit_id=package['visit'], entry_type='redeemed', amount_delta=0, units_delta=-1,
        request_key='subcent-direct-write', coverage_currency='CZK', coverage_amount_exact=Decimal('1.00'))
    db.add(entry)
    with pytest.raises(DBAPIError):
        db.commit()
    db.rollback()
    assert gifts._totals(db, package['certificate'])[1] == 3


def test_replay_and_reversal_copy_original_pair(db,package):
    _,entry,_,_=redeem(db,package)
    db.execute(update(Visit).where(Visit.id==package['visit']).values(price=99,currency_snapshot='EUR'))
    db.execute(update(VisitItem).where(VisitItem.visit_id==package['visit']).values(price=99));db.commit()
    _,again,_,_=redeem(db,package)
    assert again.id==entry.id and again.coverage_currency=='CZK' and again.coverage_amount_exact==Decimal('30.01')
    _,undo=reverse(db,package,entry)
    assert undo.coverage_currency==entry.coverage_currency and undo.coverage_amount_exact==entry.coverage_amount_exact
    assert undo.amount_delta==0 and undo.units_delta==1
    assert reverse(db,package,entry)[1].id==undo.id
    assert gifts._totals(db,package['certificate'])[1]==3


def add_services(db, p):
    services = [Service(name=f"Service {i}", specialist_id=p['owner'], business_id=p['business'],
        price=10+i, duration_minutes=30) for i in range(3)]
    db.add_all(services); db.flush()
    visit = db.get(Visit, p['visit']); visit.service_id = services[0].id
    # The legacy Visit insert trigger has already created its single placeholder item.
    db.query(VisitItem).filter_by(visit_id=visit.id).delete(synchronize_session=False)
    for position, service in enumerate(services[:2]):
        db.add(VisitItem(visit_id=visit.id, position=position, service_id=service.id,
            specialist_id=p['owner'], duration_minutes=30, price=15,
            date_start=visit.date_start+timedelta(minutes=30*position),
            date_end=visit.date_start+timedelta(minutes=30*(position+1))))
    certificate = db.get(GiftCertificate, p['certificate'])
    certificate.service_ids = [services[0].id, services[1].id]
    db.commit()
    return [row.id for row in services]


@pytest.mark.parametrize('forbidden', [False, True])
def test_full_chain_requires_every_service_to_be_allowed(db, package, forbidden):
    services = add_services(db, package)
    if forbidden:
        db.execute(update(VisitItem).where(VisitItem.visit_id==package['visit'],
            VisitItem.position==1).values(service_id=services[2])); db.commit()
        with pytest.raises(gifts.GiftError) as exc:
            redeem(db, package)
        assert exc.value.reason=='gift_scope_mismatch'
        assert gifts._totals(db, package['certificate'])[1]==3
        assert db.query(GiftLedgerEntry).filter_by(entry_type='redeemed').count()==0
    else:
        _, entry, money, units = redeem(db, package)
        assert units==1 and money==0 and entry.coverage_amount_exact==Decimal('30.01')


def test_scope_reads_fresh_items_after_collection_was_loaded(db, package):
    services = add_services(db, package)
    visit = db.get(Visit, package['visit']); assert len(visit.items)==2
    with SessionLocal() as other:
        other.execute(update(VisitItem).where(VisitItem.visit_id==visit.id,
            VisitItem.position==1).values(service_id=services[2])); other.commit()
    with pytest.raises(gifts.GiftError) as exc:
        redeem(db, package)
    assert exc.value.reason=='gift_scope_mismatch'
    assert gifts._totals(db, package['certificate'])[1]==3


@pytest.mark.parametrize('price,unit', [(0,'CZK'), (-1,'CZK'), (30.01,None), (30.01,'ZZZ'),
    (1.0000000000000002,'CZK'), (float('inf'),'CZK'), (float('nan'),'CZK')])
def test_unprovable_quote_does_not_spend_unit(db, package, price, unit):
    db.execute(update(Visit).where(Visit.id==package['visit']).values(price=price,currency_snapshot=unit)); db.commit()
    with pytest.raises(gifts.GiftError) as exc:
        redeem(db, package)
    assert exc.value.reason=='gift_package_quote_review'
    assert gifts._totals(db, package['certificate'])[1]==3


def test_low_decimal_precision_keeps_exact_saved_coverage(db, package):
    with localcontext() as ctx:
        ctx.prec=2; ctx.traps[Inexact]=True; ctx.traps[Rounded]=True
        _,entry,_,_=redeem(db,package)
    assert entry.coverage_amount_exact==Decimal('30.01')


def test_legacy_null_receipt_replays_and_reverses_without_valuation(db, package):
    original=GiftLedgerEntry(certificate_id=package['certificate'],business_id=package['business'],
        visit_id=package['visit'],entry_type='redeemed',amount_delta=0,units_delta=-1,request_key='redeem-1')
    db.add(original); db.execute(update(Visit).where(Visit.id==package['visit']).values(price=None,currency_snapshot=None)); db.commit()
    original_id=original.id
    _,replay,_,_=redeem(db,package)
    assert replay.id==original_id and replay.coverage_currency is None and replay.coverage_amount_exact is None
    _,undo=reverse(db,package,replay)
    assert undo.coverage_currency is None and undo.coverage_amount_exact is None
    assert gifts._totals(db,package['certificate'])[1]==3


@pytest.mark.parametrize('operation', ['redeem','reverse'])
def test_deferred_commit_failure_rolls_back_all_changes(db, package, operation):
    original=redeem(db,package)[1] if operation=='reverse' else None
    before=[(r.id,r.entry_type,r.units_delta,r.coverage_currency,r.coverage_amount_exact)
        for r in db.query(GiftLedgerEntry).order_by(GiftLedgerEntry.id)]
    db.execute(text("""CREATE FUNCTION reject_package_commit() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN RAISE EXCEPTION 'synthetic package commit failure'; RETURN NEW; END $$"""))
    db.execute(text("""CREATE CONSTRAINT TRIGGER reject_package_commit AFTER INSERT ON gift_ledger_entries
        DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION reject_package_commit()""")); db.commit()
    try:
        with pytest.raises(DBAPIError):
            reverse(db,package,original) if original is not None else redeem(db,package)
        assert [(r.id,r.entry_type,r.units_delta,r.coverage_currency,r.coverage_amount_exact)
            for r in db.query(GiftLedgerEntry).order_by(GiftLedgerEntry.id)]==before
        assert gifts._totals(db,package['certificate'])[1]==(2 if operation=='reverse' else 3)
    finally:
        db.rollback(); db.execute(text('DROP TRIGGER reject_package_commit ON gift_ledger_entries'))
        db.execute(text('DROP FUNCTION reject_package_commit()')); db.commit()


@pytest.mark.parametrize('same_visit', [False,True])
def test_parallel_redemptions_cannot_double_spend(db, package, same_visit):
    db.query(GiftLedgerEntry).filter_by(entry_type='issued').update({'units_delta':1})
    ids=[package['visit'],package['visit']]
    if not same_visit:
        visit=db.get(Visit,package['visit'])
        other=Visit(business_id=package['business'],client_id=visit.client_id,specialist_id=package['owner'],
            date_start=visit.date_start+timedelta(days=1),status='planned',price=20,currency_snapshot='CZK')
        db.add(other); db.flush(); ids[1]=other.id
    db.commit(); gate=Barrier(2)
    def spend(index):
        with SessionLocal() as session:
            gate.wait(timeout=10)
            try:
                return redeem(session,{**package,'visit':ids[index]},f'parallel-{index}')[1].id
            except gifts.GiftError as exc:
                return exc.reason
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(spend,range(2)))
    assert sum(isinstance(result,int) for result in results)==1, results
    assert 'gift_insufficient_balance' in results
    db.expire_all()
    assert gifts._totals(db,package['certificate'])[1]==0
    assert db.query(GiftLedgerEntry).filter_by(entry_type='redeemed').count()==1


@pytest.mark.parametrize('field,value', [('coverage_currency','EUR'),('coverage_amount_exact','9.00'),('units_delta',-2)])
def test_new_coverage_receipt_cannot_be_edited(db,package,field,value):
    _,entry,_,_=redeem(db,package)
    with pytest.raises(DBAPIError):
        db.execute(update(GiftLedgerEntry).where(GiftLedgerEntry.id==entry.id).values(**{field:value})); db.commit()
    db.rollback(); db.refresh(entry)
    assert entry.coverage_currency=='CZK' and entry.coverage_amount_exact==Decimal('30.01') and entry.units_delta==-1


def test_reversal_must_copy_exact_receipt_pair(db,package):
    _,entry,_,_=redeem(db,package)
    invalid=GiftLedgerEntry(certificate_id=package['certificate'],business_id=package['business'],
        visit_id=package['visit'],entry_type='reversed',amount_delta=0,units_delta=1,request_key='invalid-reversal',
        redeems_entry_id=entry.id,coverage_currency='CZK',coverage_amount_exact=Decimal('1.00'))
    db.add(invalid)
    with pytest.raises(DBAPIError): db.commit()
    db.rollback()
    assert gifts._totals(db,package['certificate'])[1]==2


def test_migration_does_not_backfill_legacy_receipt_and_can_rollback(db,package):
    original=GiftLedgerEntry(certificate_id=package['certificate'],business_id=package['business'],
        visit_id=package['visit'],entry_type='redeemed',amount_delta=0,units_delta=-1,request_key='legacy')
    db.add(original); db.commit(); original_id=original.id; db.close()
    config=Config('alembic.ini')
    try:
        command.downgrade(config,'0086_payroll_currency_snapshot')
        command.upgrade(config,'head')
        row=db.execute(text('SELECT coverage_currency,coverage_amount_exact FROM gift_ledger_entries WHERE id=:id'),{'id':original_id}).one()
        assert tuple(row)==(None,None)
    finally:
        db.rollback(); db.close(); command.upgrade(config,'head')


def test_migration_refuses_to_remove_new_receipt_coverage(db,package):
    _,entry,_,_=redeem(db,package); entry_id=entry.id; db.close()
    config=Config('alembic.ini')
    with pytest.raises(DBAPIError,match='Package coverage data exists'):
        command.downgrade(config,'0086_payroll_currency_snapshot')
    row=db.execute(text('SELECT coverage_currency,coverage_amount_exact FROM gift_ledger_entries WHERE id=:id'),{'id':entry_id}).one()
    assert tuple(row)==('CZK',Decimal('30.01'))
    db.rollback()


@pytest.mark.parametrize('unit', ['CZK', None])
@pytest.mark.parametrize('locale', ['ru','en','cz'])
def test_http_returns_exact_receipt_or_localized_review(client,db,package,headers_for,unit,locale):
    from app.db.models.user import User
    from app.api.core.i18n import _ as translate
    db.execute(update(Visit).where(Visit.id==package['visit']).values(currency_snapshot=unit)); db.commit()
    headers={**headers_for(db.get(User,package['owner'])), 'Accept-Language':locale}
    response=client.post('/businesses/me/gift-certificates/redeem',headers=headers,json={
        'code':package['code'],'visit_id':package['visit'],'request_key':'http-package'})
    if unit is None:
        assert response.status_code==409, response.text
        assert response.json()['error']['message']==translate('gift_package_quote_review',locale)
        assert gifts._totals(db,package['certificate'])[1]==3
    else:
        assert response.status_code==200,response.text
        body=response.json()
        assert body['coverage_currency']=='CZK' and body['coverage_amount_exact']=='30.01'
        assert body['applied_visits']==1 and body['applied_amount']==0


def test_parallel_same_request_replays_one_receipt(db,package):
    db.commit(); gate=Barrier(2)
    def spend(_index):
        with SessionLocal() as session:
            gate.wait(timeout=10)
            return redeem(session,package)[1].id
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(spend,range(2)))
    assert results[0]==results[1]
    assert db.query(GiftLedgerEntry).filter_by(entry_type='redeemed').count()==1
    assert gifts._totals(db,package['certificate'])[1]==2


def test_scope_accepts_fresh_allowed_items_after_old_collection_was_forbidden(db,package):
    services=add_services(db,package)
    db.execute(update(VisitItem).where(VisitItem.visit_id==package['visit'],
        VisitItem.position==1).values(service_id=services[2])); db.commit()
    visit=db.get(Visit,package['visit'])
    assert services[2] in {item.service_id for item in visit.items}
    with SessionLocal() as other:
        other.execute(update(VisitItem).where(VisitItem.visit_id==visit.id,
            VisitItem.position==1).values(service_id=services[1])); other.commit()
    _,entry,_,units=redeem(db,package)
    assert units==1 and entry.coverage_amount_exact==Decimal('30.01')
