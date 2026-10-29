"""Cashback analytics never add distinct or unknown currencies together."""
from datetime import date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import delete

from app.api.core import loyalty_analytics as analytics
from app.db.models.business import Business
from app.db.models.client import Client
from app.db.models.client_business_relation import ClientBusinessRelation
from app.db.models.client_cashback_balance import ClientCashbackBalance
from app.db.models.loyalty_program import LoyaltyProgram, LoyaltyPromotion, LoyaltyTransaction
from app.schemas.loyalty import LoyaltyStatisticsResponse, PointsUsageTrendsResponse, PromotionEffectivenessResponse, TopClientsResponse


@pytest.fixture
def world(db):
    business=Business(name='Currency analytics',business_type='salon',currency='EUR')
    other=Business(name='Foreign analytics',business_type='salon',currency='EUR')
    client=Client(full_name='Synthetic member');wallet_only=Client(full_name='Wallet only')
    db.add_all([business,other,client,wallet_only]);db.flush()
    program=LoyaltyProgram(business_id=business.id,program_type='hybrid')
    db.add(program);db.flush()
    promo=LoyaltyPromotion(program_id=program.id,name='Mixed currency promotion',promotion_type='bonus_points',
        start_date=date(2026,1,1),end_date=date(2026,12,31))
    db.add(promo);db.flush()
    relation=ClientBusinessRelation(client_id=client.id,business_id=business.id,loyalty_points=30,loyalty_cashback=7)
    db.add(relation)
    for unit,earned,spent,mirror in [('CZK','10.10','2.10',999),('EUR','20.20','5.20',888),(None,'7','1',7)]:
        for kind,amount,points in [('earned',Decimal(earned),5),('spent',-Decimal(spent),-2)]:
            exact=amount if unit else None
            db.add(LoyaltyTransaction(client_id=client.id,business_id=business.id,program_id=program.id,
                promotion_id=promo.id,transaction_type=kind,points_amount=points,points_balance_after=30,
                cashback_amount=(mirror if kind=='earned' else -mirror) if unit else float(amount),
                cashback_balance_after=7,cashback_currency=unit,cashback_amount_exact=exact,
                cashback_balance_after_exact=Decimal('50') if unit else None,created_at=datetime(2026,10,27,12)))
    db.add_all([
        ClientCashbackBalance(client_id=client.id,business_id=business.id,currency='CZK',balance=Decimal('6.10')),
        ClientCashbackBalance(client_id=client.id,business_id=business.id,currency='EUR',balance=Decimal('8.20')),
        ClientCashbackBalance(client_id=wallet_only.id,business_id=business.id,currency='EUR',balance=Decimal('9.30')),
        ClientCashbackBalance(client_id=client.id,business_id=other.id,currency='USD',balance=Decimal('99999')),
    ])
    db.commit()
    return {'business':business.id,'other':other.id,'client':client.id,'wallet_only':wallet_only.id,
            'program':program.id,'promo':promo.id,'relation':relation.id}


def slices(value):return {row['currency']:row for row in value}


def test_statistics_preserve_exact_currency_slices_and_unknown_history(db,world):
    result=analytics.get_loyalty_statistics(db,world['business'])
    assert result['total_cashback_earned'] is None
    assert result['total_cashback_spent'] is None and result['active_cashback_balance'] is None
    assert result['cashback_redemption_rate'] is None and result['cashback_currency'] is None
    data=slices(result['cashback_by_currency'])
    assert data['CZK']['earned']==Decimal('10.10') and data['CZK']['spent']==Decimal('2.10')
    assert data['EUR']['balance']==Decimal('17.50')
    assert data[None]['earned'] is None and data[None]['spent'] is None and data[None]['balance'] is None
    assert data[None]['redemption_rate'] is None
    assert result['total_points_earned']==15 and result['total_points_spent']==6
    assert result['active_points_balance']==30 and result['total_participants']==1
    assert 'USD' not in data
    LoyaltyStatisticsResponse.model_validate(result)


def test_single_known_unit_can_expose_a_labelled_scalar_from_exact_values(db,world):
    db.execute(delete(LoyaltyTransaction).where(LoyaltyTransaction.cashback_currency.is_distinct_from('CZK')))
    db.execute(delete(ClientCashbackBalance).where(ClientCashbackBalance.currency!='CZK'))
    db.get(ClientBusinessRelation,world['relation']).loyalty_cashback=0;db.commit()
    result=analytics.get_loyalty_statistics(db,world['business'])
    assert result['cashback_currency']=='CZK'
    assert result['total_cashback_earned']==Decimal('10.10')
    assert result['total_cashback_spent']==Decimal('2.10')
    assert result['active_cashback_balance']==Decimal('6.10')
    assert result['cashback_redemption_rate']==pytest.approx(2.1/10.1*100)


def test_unknown_money_is_not_summed_into_an_unlabelled_scalar(db,world):
    db.execute(delete(LoyaltyTransaction).where(LoyaltyTransaction.cashback_currency.is_not(None)))
    db.execute(delete(ClientCashbackBalance));db.commit()
    result=analytics.get_loyalty_statistics(db,world['business'])
    assert result['total_cashback_earned'] is None and result['active_cashback_balance'] is None
    assert result['cashback_by_currency']==[{'currency':None,'earned':None,'spent':None,'balance':None,'redemption_rate':None}]


def test_program_deletion_does_not_hide_financial_history(db,world):
    db.execute(delete(LoyaltyProgram).where(LoyaltyProgram.id==world['program']));db.commit()
    result=analytics.get_loyalty_statistics(db,world['business'])
    assert result['total_points_earned']==15
    assert slices(result['cashback_by_currency'])['CZK']['balance']==Decimal('6.10')


def test_weekly_trends_keep_nonmonetary_counts_and_split_money(db,world):
    data=analytics.get_points_usage_trends(db,world['business'],group_by='week')
    assert len(data)==1 and data[0]['period']=='2026-10-26'
    row=data[0];assert row['transaction_count']==6 and row['points_earned']==15 and row['points_spent']==6
    assert row['cashback_earned'] is None and row['cashback_spent'] is None and row['cashback_currency'] is None
    by_currency=slices(row['cashback_by_currency'])
    assert by_currency['EUR']['earned']==Decimal('20.20') and by_currency['CZK']['spent']==Decimal('2.10')
    assert by_currency[None]['earned'] is None
    PointsUsageTrendsResponse.model_validate({'trends':data})


def test_promotion_totals_do_not_use_float_mirrors_or_add_unknown_money(db,world):
    data=analytics.get_promotion_effectiveness(db,world['business'])
    row=data[0];assert row['usage_count']==6 and row['unique_clients']==1 and row['total_points_awarded']==15
    assert row['total_cashback_awarded'] is None
    assert slices(row['cashback_by_currency'])['EUR']['amount']==Decimal('20.20')
    assert slices(row['cashback_by_currency'])[None]['amount'] is None
    PromotionEffectivenessResponse.model_validate({'promotions':data})


def test_top_clients_include_wallet_only_members_without_summing_currencies(db,world):
    rows=analytics.get_top_clients_by_points(db,world['business'],limit=10)
    assert [row['client_id'] for row in rows]==[world['client'],world['wallet_only']]
    first,second=rows
    assert first['cashback'] is None and first['cashback_currency'] is None
    assert slices(first['cashback_balances'])[None]=={'currency':None,'amount':Decimal('7.0'),'requires_review':True}
    assert second['cashback']==Decimal('9.30') and second['cashback_currency']=='EUR'
    assert len(first['cashback_balances'])==3
    TopClientsResponse.model_validate({'clients':rows})


def test_empty_statistics_stay_zero_without_a_currency(db):
    business=Business(name='Empty analytics',business_type='salon');db.add(business);db.commit()
    result=analytics.get_loyalty_statistics(db,business.id)
    assert result['total_cashback_earned']==0 and result['active_cashback_balance']==0
    assert result['cashback_currency'] is None and result['cashback_by_currency']==[]
    assert result['cashback_redemption_rate']==0


def test_known_cashback_math_is_independent_of_the_callers_decimal_context(db,world):
    from decimal import localcontext
    db.execute(delete(LoyaltyTransaction))
    db.execute(delete(ClientCashbackBalance))
    db.get(ClientBusinessRelation,world['relation']).loyalty_cashback=0
    for kind,amount in [('earned','1000.01'),('earned','0.01'),('spent','-1000.01'),('spent','-0.01')]:
        db.add(LoyaltyTransaction(client_id=world['client'],business_id=world['business'],
            promotion_id=world['promo'],transaction_type=kind,points_amount=0,points_balance_after=0,
            cashback_amount=999,cashback_balance_after=0,cashback_currency='CZK',
            cashback_amount_exact=Decimal(amount),cashback_balance_after_exact=Decimal('0'),
            created_at=datetime(2026,10,27,12)))
    db.add_all([
        ClientCashbackBalance(client_id=world['client'],business_id=world['business'],currency='CZK',balance=Decimal('1000.01')),
        ClientCashbackBalance(client_id=world['wallet_only'],business_id=world['business'],currency='CZK',balance=Decimal('0.01')),
    ])
    db.commit()
    with localcontext() as context:
        context.prec=2
        stats=analytics.get_loyalty_statistics(db,world['business'])
        trends=analytics.get_points_usage_trends(db,world['business'])
        promotions=analytics.get_promotion_effectiveness(db,world['business'])
    assert stats['total_cashback_earned']==Decimal('1000.02')
    assert stats['total_cashback_spent']==Decimal('1000.02')
    assert stats['active_cashback_balance']==Decimal('1000.02')
    assert stats['cashback_redemption_rate']==100.0
    assert trends[0]['cashback_earned']==Decimal('1000.02') and trends[0]['cashback_spent']==Decimal('1000.02')
    assert promotions[0]['total_cashback_awarded']==Decimal('1000.02')


def test_promotion_report_reads_the_filtered_business_ledger_only_once(db,world):
    from sqlalchemy import event
    db.add(LoyaltyPromotion(program_id=world['program'],name='Another promotion',promotion_type='bonus_points',
        start_date=date(2026,1,1),end_date=date(2026,12,31)))
    db.commit();statements=[]
    def before(conn,cursor,statement,*args):
        if 'FROM loyalty_transactions' in statement:
            statements.append(statement)
    event.listen(db.get_bind(),'before_cursor_execute',before)
    try:
        result=analytics.get_promotion_effectiveness(db,world['business'],datetime(2026,1,1),datetime(2026,12,31))
    finally:
        event.remove(db.get_bind(),'before_cursor_execute',before)
    assert len(result)==2
    assert len(statements)==1, f'Expected one business/date ledger read for both promotions, got {len(statements)}'
    assert 'loyalty_transactions.business_id =' in statements[0]
    assert 'loyalty_transactions.created_at >=' in statements[0]
    assert 'loyalty_transactions.created_at <=' in statements[0]
