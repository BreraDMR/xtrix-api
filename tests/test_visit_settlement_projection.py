"""Read-only evidence never turns an unknown receipt into a payable balance."""
from dataclasses import FrozenInstanceError, asdict
from datetime import datetime, timedelta
from decimal import Decimal, localcontext, Inexact, Rounded

import pytest
from sqlalchemy import event, text, update
from sqlalchemy.orm import Session

from app.api.core.visit_settlement_projection import SettlementScope, read_settlement
from app.db.models.business import Business
from app.db.models.client import Client
from app.db.models.gift import GiftOffer, GiftOrder, GiftCertificate, GiftLedgerEntry
from app.db.models.loyalty_program import LoyaltyTransaction
from app.db.models.service_payment import ServicePaymentAttempt, ServicePaymentEvent, ServicePaymentRefund
from app.db.models.visit import Visit
from app.db.models.visit_item import VisitItem

NOW=datetime(2026,1,1)


@pytest.fixture
def world(db,make_user):
    worker=make_user('business_owner')
    business=Business(name='Settlement',business_type='salon',currency='EUR')
    client=Client(full_name='Settlement client')
    db.add_all([business,client]);db.flush();worker.business_id=business.id
    visit=Visit(business_id=business.id,client_id=client.id,specialist_id=worker.id,
        date_start=NOW,price=Decimal('1000.01'),currency_snapshot='EUR',status='planned')
    db.add(visit);db.commit()
    return {'scope':SettlementScope(visit.id,business.id,client.id,worker.id),'visit':visit}


def attempt(db,w,key='a',status='paid',value='20.01',paid=True):
    scope=w['scope']
    row=ServicePaymentAttempt(business_id=scope.business_id,visit_id=scope.id,provider='sandbox',merchant='test',
        request_key=key,public_ref=key,public_token_hash='0'*64,status=status,currency='EUR',amount=Decimal(value),
        remaining_amount=Decimal('999.99'),refunded_amount=0,provider_ref='provider-'+key,
        expires_at=NOW+timedelta(hours=1),paid_at=NOW if status=='paid' else None)
    db.add(row);db.flush()
    if paid:
        payment_event(db,row,'paid-'+key,'payment.paid')
    db.commit();return row


def payment_event(db,row,key,kind,status=None):
    db.add(ServicePaymentEvent(attempt_id=row.id,business_id=row.business_id,provider='sandbox',
        provider_event_id=key,event_type=kind,status=status or row.status,source='internal' if kind=='refund.succeeded' else 'webhook',payload_hash='1'*64))


def refund(db,row,key='r',value='5.01'):
    value=Decimal(value)
    db.add(ServicePaymentRefund(attempt_id=row.id,business_id=row.business_id,provider='sandbox',
        refund_key=key,provider_refund_ref=key,amount=value,currency=row.currency,status='succeeded'))
    row.refunded_amount=Decimal(str(row.refunded_amount))+value;row.status='partially_refunded'
    payment_event(db,row,'refund-'+key,'refund.succeeded')
    db.commit()


def bonus(db,w,value='10.01',kind='spent',**overrides):
    scope=w['scope']
    row=LoyaltyTransaction(client_id=scope.client_id,business_id=scope.business_id,visit_id=scope.id,
        transaction_type=kind,points_amount=-1 if kind=='spent' else 99,points_balance_after=0,
        cashback_amount=0,cashback_balance_after=0,
        discount_currency='EUR' if kind=='spent' else None,
        discount_amount_exact=Decimal(value) if kind=='spent' else None)
    for key,val in overrides.items():setattr(row,key,val)
    db.add(row);db.commit();return row


def gift(db,w,key='gift',value='7.01',kind='money',coverage=None,certificate_unit='EUR'):
    scope=w['scope']
    offer=GiftOffer(business_id=scope.business_id,name=key,kind=kind,price=10,currency='EUR',validity_days=365)
    db.add(offer);db.flush()
    order=GiftOrder(business_id=scope.business_id,offer_id=offer.id,provider='sandbox',merchant='test',
        request_key=key,request_hash='0'*64,public_ref=key,public_token_hash='0'*64,status='paid',
        offer_snapshot={},amount=10,currency='EUR',buyer_name='Buyer',buyer_email='buyer@example.com',expires_at=NOW)
    db.add(order);db.flush()
    certificate=GiftCertificate(business_id=scope.business_id,offer_id=offer.id,order_id=order.id,
        code=key,code_hash=key,code_last4=key[-4:],kind=kind,status='active',issuer_name='Issuer',
        offer_name=key,currency=certificate_unit,face_value=100,visits_total=10,recipient_name='Recipient',
        buyer_name='Buyer',buyer_email='buyer@example.com',expires_at=NOW+timedelta(days=365))
    db.add(certificate);db.flush()
    row=GiftLedgerEntry(certificate_id=certificate.id,business_id=scope.business_id,entry_type='redeemed',
        amount_delta=-Decimal(value) if kind=='money' else 0,units_delta=0 if kind=='money' else -1,
        request_key=key,visit_id=scope.id,
        coverage_currency=coverage[0] if coverage else None,
        coverage_amount_exact=Decimal(coverage[1]) if coverage else None)
    db.add(row);db.commit();return certificate,row


def reverse(db,row,value=None):
    entry=GiftLedgerEntry(certificate_id=row.certificate_id,business_id=row.business_id,entry_type='reversed',
        amount_delta=-row.amount_delta if value is None else Decimal(value),units_delta=-row.units_delta,
        request_key='reverse-'+str(row.id),visit_id=row.visit_id,redeems_entry_id=row.id,
        coverage_currency=row.coverage_currency,coverage_amount_exact=row.coverage_amount_exact)
    db.add(entry);db.commit();return entry


def test_empty_known_quote_is_factual_and_immutable(db,world):
    result=read_settlement(db,world['scope'])
    assert result.gross_quote==result.price_after_loyalty==Decimal('1000.01')
    assert result.loyalty_discount==result.provider_received==result.provider_refunded==result.provider_net==0
    assert result.gift_money_coverage==result.gift_units_coverage==0
    assert result.gift_package_coverage == 0
    assert not result.requires_review and result.reasons==()
    assert result.outstanding==Decimal('1000.01') and result.overpayment==0 and result.outstanding_reason is None
    with pytest.raises(FrozenInstanceError):result.currency='CZK'


def test_duplicate_paid_events_and_refund_sources_count_once(db,world):
    row=attempt(db,world)
    payment_event(db,row,'another-paid','payment.paid');db.commit();refund(db,row)
    result=read_settlement(db,world['scope'])
    assert result.provider_received==Decimal('20.01')
    assert result.provider_refunded==Decimal('5.01') and result.provider_net==15
    assert not result.requires_review
    assert result.outstanding is None


def test_child_families_never_cartesian_multiply(db,world):
    for i in range(2):
        row=attempt(db,world,str(i),value='20.00');refund(db,row,str(i),value='1.00')
    for i in range(3):gift(db,world,str(i),value='2.00')
    for _ in range(4):bonus(db,world,value='3.00')
    result=read_settlement(db,world['scope'])
    assert result.provider_received==40 and result.provider_refunded==2 and result.provider_net==38
    assert result.gift_money_coverage==6 and result.loyalty_discount==12
    assert result.price_after_loyalty==Decimal('988.01')
    assert 'mixed_settlement_history' in result.reasons


def test_stored_bonus_discount_not_earned_points_or_current_rate(db,world):
    bonus(db,world);bonus(db,world,kind='earned')
    db.get(Business,world['scope'].business_id).currency='CZK';db.commit()
    result=read_settlement(db,world['scope'])
    assert result.currency=='EUR' and result.loyalty_discount==Decimal('10.01')
    assert result.price_after_loyalty==990 and result.provider_received==0


@pytest.mark.parametrize('case',['missing_snapshot','foreign_client','foreign_business','wrong_unit','overallocated'])
def test_bad_bonus_history_is_unknown_not_zero(db,world,case):
    overrides={}
    if case=='missing_snapshot':overrides.update(discount_currency=None,discount_amount_exact=None)
    elif case=='foreign_client':
        other=Client(full_name='Foreign');db.add(other);db.flush();overrides['client_id']=other.id
    elif case=='foreign_business':
        other=Business(name='Foreign',business_type='salon');db.add(other);db.flush();overrides['business_id']=other.id
    elif case=='wrong_unit':overrides['discount_currency']='CZK'
    bonus(db,world,value='2000' if case=='overallocated' else '10.01',**overrides)
    result=read_settlement(db,world['scope'])
    assert result.price_after_loyalty is None and result.requires_review
    if case!='overallocated':assert result.loyalty_discount is None
    assert result.provider_received==0 and result.outstanding is None
    assert 'Foreign' not in str(asdict(result))


@pytest.mark.parametrize('state',['pending','failed','expired','cancelled','reconcile'])
def test_attempt_status_never_fabricates_a_receipt(db,world,state):
    row=attempt(db,world,status=state,paid=state=='reconcile')
    result=read_settlement(db,world['scope'])
    assert result.has_pending_checkout==(state=='pending')
    if state=='reconcile':
        assert result.provider_received is None and result.has_reconciliation
        assert 'provider_history_review' in result.reasons
    else:assert result.provider_received==0 and not result.requires_review
    # an attempt that never paid is not money: pending waits, the rest owe the full price
    if state in ('pending','reconcile'):assert result.outstanding is None
    else:assert result.outstanding==Decimal('1000.01') and result.outstanding_reason is None


@pytest.mark.parametrize('case',['no_paid_event','foreign_business','event_business','event_provider','event_status','event_source','refund_currency','refund_aggregate','refund_without_event','excess_refund'])
def test_broken_provider_evidence_cannot_be_a_known_net(db,world,case):
    row=attempt(db,world,paid=case!='no_paid_event')
    if case in ('refund_currency','refund_aggregate','refund_without_event','excess_refund'):
        refund(db,row)
        saved=db.query(ServicePaymentRefund).one()
        if case=='refund_currency':saved.currency='CZK'
        elif case=='refund_aggregate':row.refunded_amount=Decimal('1.00')
        elif case=='refund_without_event':db.query(ServicePaymentEvent).filter_by(event_type='refund.succeeded').delete()
        else:saved.amount=100;row.refunded_amount=100
    elif case in ('foreign_business','event_business'):
        other=Business(name='Foreign provider business',business_type='salon');db.add(other);db.flush()
        if case=='foreign_business':row.business_id=other.id
        else:db.query(ServicePaymentEvent).one().business_id=other.id
    elif case=='event_provider':db.query(ServicePaymentEvent).one().provider='another'
    elif case=='event_status':db.query(ServicePaymentEvent).one().status='pending'
    elif case=='event_source':db.query(ServicePaymentEvent).one().source='unverified'
    db.commit();result=read_settlement(db,world['scope'])
    assert result.provider_net is None and result.requires_review
    assert 'provider_history_review' in result.reasons
    if case.startswith('refund_') or case=='excess_refund':
        assert result.provider_received==Decimal('20.01')
    assert 'Foreign' not in str(asdict(result))


def test_money_gift_reversal_and_package_have_different_meaning(db,world):
    _,row=gift(db,world);reverse(db,row)
    first=read_settlement(db,world['scope'])
    assert first.gift_money_coverage==0 and not first.requires_review
    gift(db,world,'package',kind='package')
    result=read_settlement(db,world['scope'])
    assert result.gift_units_coverage==1 and result.gift_money_coverage==0
    assert 'gift_package_value_unknown' in result.reasons and result.outstanding is None


@pytest.mark.parametrize('case',['partial_reverse','wrong_reference','foreign_certificate','wrong_unit','bad_units'])
def test_broken_gift_history_never_infers_coverage(db,world,case):
    cert,row=gift(db,world)
    if case=='partial_reverse':reverse(db,row,value=1)
    elif case=='wrong_reference':
        another_visit=Visit(business_id=world['scope'].business_id,client_id=world['scope'].client_id,
            specialist_id=world['scope'].specialist_id,date_start=NOW+timedelta(days=1),price=20,currency_snapshot='EUR')
        db.add(another_visit);db.flush();row.visit_id=another_visit.id;db.commit()
        reversed_row=reverse(db,row);reversed_row.visit_id=world['scope'].id
    elif case=='foreign_certificate':
        foreign=Business(name='Foreign gift business',business_type='salon');db.add(foreign);db.flush();cert.business_id=foreign.id
    elif case=='wrong_unit':cert.currency='CZK'
    else:row.units_delta=-1
    db.commit();result=read_settlement(db,world['scope'])
    assert result.gift_money_coverage is None and result.requires_review
    assert 'gift_history_review' in result.reasons


def test_manual_marker_survives_current_method_and_does_not_guess_amount(db,world):
    visit=world['visit'];visit.status='completed';visit.payment_method='cash';db.commit()
    visit.status='planned';visit.payment_method='bonus';db.commit()
    result=read_settlement(db,world['scope'])
    assert result.manual_payment_recorded and result.provider_received==0
    assert 'manual_amount_unknown' in result.reasons and result.outstanding is None


def test_snapshot_is_fresh_one_select_no_flush_commit_or_locks(db,world):
    stale=world['visit'];assert stale.price==1000.01
    scope=world['scope'];engine=db.get_bind()
    with Session(engine) as writer:
        writer.execute(update(Visit).where(Visit.id==scope.id).values(price=20));
        writer.execute(update(VisitItem).where(VisitItem.visit_id==scope.id).values(price=20));writer.commit()
    pending=Client(full_name='Pending should not flush');db.add(pending);statements=[];changed=[]
    def change_after_select(conn,cursor,sql,parameters,context,executemany):
        statements.append(sql)
        if changed or not sql.lstrip().upper().startswith('SELECT'):return
        changed.append(True)
        with Session(engine) as writer:
            writer.execute(update(Visit).where(Visit.id==scope.id).values(price=30))
            writer.execute(update(VisitItem).where(VisitItem.visit_id==scope.id).values(price=30))
            writer.add(LoyaltyTransaction(client_id=scope.client_id,business_id=scope.business_id,visit_id=scope.id,
                transaction_type='spent',points_amount=-1,points_balance_after=0,cashback_amount=0,
                cashback_balance_after=0,discount_currency='EUR',discount_amount_exact=3));writer.commit()
    event.listen(engine,'after_cursor_execute',change_after_select)
    try:
        with localcontext() as ctx:
            ctx.prec=2;ctx.traps[Inexact]=True;ctx.traps[Rounded]=True
            result=read_settlement(db,scope)
    finally:event.remove(engine,'after_cursor_execute',change_after_select)
    # The event hook also sees the independent writer's statements; inspect the reader separately.
    selects=[sql for sql in statements if sql.lstrip().upper().startswith('SELECT')]
    assert len(selects)==1
    assert all(token not in selects[0].upper() for token in ('FOR UPDATE','FOR SHARE'))
    assert result.gross_quote==20 and result.loyalty_discount==0 and result.price_after_loyalty==20
    assert pending.id is None and stale.price==1000.01
    assert db.in_transaction();db.rollback()
    fresh=read_settlement(db,scope)
    assert fresh.gross_quote==30 and fresh.loyalty_discount==3


def test_low_context_exact_decimal_and_scope_retarget(db,world):
    row=attempt(db,world,value='999.99');refund(db,row,value='10.01');bonus(db,world,value='10.01')
    with localcontext() as ctx:
        ctx.prec=2;ctx.traps[Inexact]=True;ctx.traps[Rounded]=True
        result=read_settlement(db,world['scope'])
    assert result.provider_net==Decimal('989.98') and result.price_after_loyalty==990
    original=world['scope'];other=Client(full_name='Retargeted');db.add(other);db.flush()
    db.execute(update(Visit).where(Visit.id==original.id).values(client_id=other.id));db.commit()
    assert read_settlement(db,original) is None


@pytest.mark.parametrize('case',['unknown_unit','unsupported_unit','negative','nan','fraction','zero','deleted'])
def test_bad_or_zero_quotes_keep_knowledge_boundary(db,world,case):
    scope=world['scope'];visit=world['visit']
    if case=='unknown_unit':visit.currency_snapshot=None
    elif case=='unsupported_unit':visit.currency_snapshot='ZZZ'
    elif case=='deleted':visit.deleted_at=NOW
    else:
        value={'negative':-1,'nan':float('nan'),'fraction':1.001,'zero':0}[case]
        db.execute(update(Visit).where(Visit.id==scope.id).values(price=value))
        db.execute(update(VisitItem).where(VisitItem.visit_id==scope.id).values(price=value))
    db.commit();result=read_settlement(db,scope)
    if case=='deleted':assert result is None
    elif case=='zero':assert result.gross_quote==result.price_after_loyalty==0 and not result.requires_review
    else:assert result.requires_review and result.price_after_loyalty is None


def test_dirty_financial_identity_is_not_flushed_or_committed(db,world,monkeypatch):
    row=attempt(db,world)
    row.amount=Decimal('99.99')
    def forbidden(*args,**kwargs):raise AssertionError('The projection attempted a write')
    monkeypatch.setattr(db,'flush',forbidden)
    monkeypatch.setattr(db,'commit',forbidden)
    result=read_settlement(db,world['scope'])
    assert result.provider_received==Decimal('20.01')
    assert row.amount==Decimal('99.99') and row in db.dirty
    db.rollback()


def test_an_orm_hint_is_not_an_authorized_scalar_scope(db,world):
    with pytest.raises(ValueError):read_settlement(db,world['visit'])
    with pytest.raises(ValueError):SettlementScope(True,1,1,1)


def test_saved_package_value_is_not_the_certificate_price_or_current_business_currency(db, world):
    certificate, receipt = gift(db, world, kind='package', coverage=('EUR', '1000.01'), certificate_unit='CZK')
    db.get(Business, world['scope'].business_id).currency = 'CZK'
    certificate.face_value = 999
    db.commit()
    facts = read_settlement(db, world['scope'])
    assert facts.gift_package_coverage == Decimal('1000.01')
    assert facts.gift_units_coverage == 1 and facts.gift_money_coverage == 0
    assert 'gift_package_value_unknown' not in facts.reasons
    assert not facts.requires_review
    assert facts.outstanding == facts.overpayment == 0 and facts.outstanding_reason is None
    assert receipt.coverage_currency == facts.currency == 'EUR'


def test_reversed_package_has_zero_active_coverage_without_repricing_the_receipt(db, world):
    _, receipt = gift(db, world, kind='package', coverage=('EUR', '1000.01'))
    reverse(db, receipt)
    world['visit'].price = 20
    db.commit()
    facts = read_settlement(db, world['scope'])
    assert facts.gross_quote == 20
    assert facts.gift_units_coverage == facts.gift_package_coverage == 0
    assert not facts.requires_review and facts.outstanding == 20 and facts.overpayment == 0
    assert receipt.coverage_amount_exact == Decimal('1000.01')


def test_legacy_package_value_remains_unknown_until_its_exact_reversal(db, world):
    _, receipt = gift(db, world, kind='package')
    first = read_settlement(db, world['scope'])
    assert first.gift_package_coverage is None
    assert 'gift_package_value_unknown' in first.reasons
    reverse(db, receipt)
    second = read_settlement(db, world['scope'])
    assert second.gift_package_coverage == second.gift_units_coverage == 0
    assert not second.requires_review


@pytest.mark.parametrize('case', ['missing_amount', 'missing_unit', 'wrong_unit', 'zero', 'subcent',
                                  'money_metadata', 'wrong_reversal', 'duplicate_unit'])
def test_broken_package_coverage_is_reviewed_without_guessing(case):
    from app.api.core.visit_settlement_projection import _gifts
    row = {'id': 1, 'certificate': 1, 'business': 7, 'certificate_business': 7,
           'type': 'redeemed', 'amount': '0.00', 'units': -1, 'reverses': None,
           'kind': 'package', 'currency': 'CZK', 'coverage_currency': 'EUR', 'coverage_amount': '1000.01'}
    rows = [row]
    if case == 'missing_amount': row['coverage_amount'] = None
    elif case == 'missing_unit': row['coverage_currency'] = None
    elif case == 'wrong_unit': row['coverage_currency'] = 'CZK'
    elif case == 'zero': row['coverage_amount'] = '0.00'
    elif case == 'subcent': row['coverage_amount'] = '1.0000000000000002'
    elif case == 'money_metadata': row.update(kind='money', amount='-1.00', units=0, currency='EUR')
    elif case == 'wrong_reversal': rows.append({**row, 'id': 2, 'type': 'reversed', 'units': 1,
                                               'reverses': 1, 'coverage_amount': '1.00'})
    else: rows.append({**row, 'id': 2})
    assert _gifts(rows, 7, 'EUR') == (None, None, None, True)
