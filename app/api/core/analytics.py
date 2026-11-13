from decimal import Decimal
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional
from sqlalchemy.orm import Session
from sqlalchemy import func, extract, select, case, and_, cast, String, DateTime, Integer, BigInteger, null, literal, union_all
from app.db.models.visit import Visit
from app.db.models.service import Service
from app.db.models.client import Client
from app.db.models.client_business_relation import ClientBusinessRelation
from app.api.core.clock import utcnow
from app.api.core.money_slices import (
    as_float, average, currency_order, grouped_quotes, money_columns, money_slice, quote_cents, quote_currency,
    single_currency,
)
def get_visit_statistics(
    db: Session,
    specialist_id: Optional[int] = None,
    business_id: Optional[int] = None,
    client_id: Optional[int] = None,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
) -> Dict[str, Any]:
    q = db.query(Visit)
    if specialist_id:
        q = q.filter(Visit.specialist_id == specialist_id)
    if business_id:
        q = q.filter(Visit.business_id == business_id)
    if client_id:
        q = q.filter(Visit.client_id == client_id)
    if date_from:
        q = q.filter(Visit.date_start >= date_from)
    if date_to:
        q = q.filter(Visit.date_start <= date_to)
    total_visits = q.count()
    status_counts = (
        db.query(Visit.status, func.count(Visit.id).label('count'))
        .filter(*[
            Visit.specialist_id == specialist_id if specialist_id else True,
            Visit.business_id == business_id if business_id else True,
            Visit.client_id == client_id if client_id else True,
            Visit.date_start >= date_from if date_from else True,
            Visit.date_start <= date_to if date_to else True,
        ])
        .group_by(Visit.status)
        .all()
    )
    visits_by_status = {status: count for status, count in status_counts}
    base_filters = []
    if specialist_id:
        base_filters.append(Visit.specialist_id == specialist_id)
    if business_id:
        base_filters.append(Visit.business_id == business_id)
    if client_id:
        base_filters.append(Visit.client_id == client_id)
    if date_from:
        base_filters.append(Visit.date_start >= date_from)
    if date_to:
        base_filters.append(Visit.date_start <= date_to)
    monthly_visits = (
        db.query(
            extract('year', Visit.date_start).label('year'),
            extract('month', Visit.date_start).label('month'),
            func.count(Visit.id).label('count')
        )
        .filter(*base_filters)
        .group_by('year', 'month')
        .order_by('year', 'month')
        .all()
    )
    monthly_data = [
        {
            'year': int(year),
            'month': int(month),
            'count': int(count),
            'period': f"{int(year)}-{int(month):02d}",
        }
        for year, month, count in monthly_visits
    ]
    duration_filters = base_filters + [Visit.date_end.isnot(None)]
    avg_duration = (
        db.query(
            func.avg(
                func.extract('epoch', Visit.date_end - Visit.date_start) / 60
            ).label('avg_minutes')
        )
        .filter(*duration_filters)
        .scalar()
    )
    return {
        'total_visits': total_visits,
        'visits_by_status': visits_by_status,
        'monthly_visits': monthly_data,
        'average_duration_minutes': round(avg_duration, 2) if avg_duration else None,
    }
def get_financial_report(
    db: Session,
    specialist_id: Optional[int] = None,
    business_id: Optional[int] = None,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    group_by: str = 'month',
) -> Dict[str, Any]:
    """Report stored service quotes before loyalty, never money received or FX.

    PostgreSQL groups integer-cent Numeric values in one statement snapshot.
    Unknown units are counted but never summed. One invalid stored amount makes
    its slice unavailable instead of quietly removing that visit from a total.
    """
    if group_by not in {'day', 'week', 'month', 'year'}:
        raise ValueError('Unsupported financial report period')
    # Same NaN/infinity-safe cent expressions as the other money analytics.
    currency = quote_currency()
    cents = quote_cents(currency)
    filters = [Visit.deleted_at.is_(None)]
    if specialist_id:
        filters.append(Visit.specialist_id == specialist_id)
    if business_id:
        filters.append(Visit.business_id == business_id)
    if date_from:
        filters.append(Visit.date_start >= date_from)
    if date_to:
        filters.append(Visit.date_start <= date_to)
    quotes = select(Visit.client_id.label('client_id'), Visit.date_start.label('start'),
        func.coalesce(Visit.status, 'unknown').label('status'), currency.label('currency'),
        cents.label('cents')).where(*filters).cte('stored_quotes')

    def columns():
        return money_columns(quotes.c.cents)

    def slice_value(row):
        return money_slice(row['currency'], row['count'], row['valid_count'], row['total_cents'])

    compatibility = as_float

    def summary(values):
        values = sorted(values, key=lambda row: currency_order(row['currency']))
        single = values[0] if len(values) == 1 and values[0]['currency'] is not None else None
        return {'revenue': compatibility(single['amount']) if single else (0.0 if not values else None),
                'currency': single['currency'] if single else None,
                'count': sum(row['count'] for row in values), 'by_currency': values}

    empty_status = cast(null(), String()).label('status')
    empty_period = cast(null(), DateTime()).label('period')
    empty_client = cast(null(), Integer()).label('client_id')
    empty_name = cast(null(), String()).label('client_name')
    empty_position = cast(null(), BigInteger()).label('position')
    totals_query = select(literal('total').label('kind'), quotes.c.currency,
        empty_status, empty_period, empty_client, empty_name, *columns(), empty_position
        ).group_by(quotes.c.currency)
    status_query = select(literal('status'), quotes.c.currency,
        quotes.c.status, empty_period, empty_client, empty_name, *columns(), empty_position
        ).group_by(quotes.c.status, quotes.c.currency)
    period = func.date_trunc(group_by, quotes.c.start)
    period_query = select(literal('period'), quotes.c.currency,
        empty_status, cast(period, DateTime()), empty_client, empty_name, *columns(), empty_position
        ).group_by(period, quotes.c.currency)
    clients = select(quotes.c.currency, quotes.c.client_id, Client.full_name.label('client_name'), *columns()
        ).join(Client, Client.id == quotes.c.client_id).group_by(
            quotes.c.currency, quotes.c.client_id, Client.full_name).cte('client_quotes')
    ranked_value = case((and_(clients.c.currency.is_not(None), clients.c.valid_count == clients.c.count),
                         clients.c.total_cents), else_=None)
    ranked = select(*clients.c, func.row_number().over(partition_by=clients.c.currency,
        order_by=(ranked_value.desc().nullslast(), clients.c.client_id)).label('position')).subquery('ranked_quotes')
    clients_query = select(literal('client'), ranked.c.currency,
        empty_status, empty_period, ranked.c.client_id, ranked.c.client_name,
        ranked.c.count, ranked.c.valid_count, ranked.c.total_cents, ranked.c.position
        ).where(ranked.c.position <= 10)
    # Auth may already have opened READ COMMITTED. One UNION statement gives all
    # dimensions the same snapshot without changing that caller transaction.
    with db.no_autoflush:
        rows = db.execute(union_all(totals_query, status_query, period_query, clients_query)).mappings().all()
    totals = [row for row in rows if row['kind'] == 'total']
    status_rows = [row for row in rows if row['kind'] == 'status']
    period_rows = [row for row in rows if row['kind'] == 'period']
    client_rows = sorted((row for row in rows if row['kind'] == 'client'),
        key=lambda row: (row['currency'] is None, row['currency'] or '', row['position']))

    by_currency = [slice_value(row) for row in totals]
    overall = summary(by_currency)
    statuses = {}
    for row in status_rows:
        statuses.setdefault(row['status'], []).append(slice_value(row))
    statuses = {key: summary(values) for key, values in statuses.items()}
    periods = {}
    for row in period_rows:
        periods.setdefault(row['period'], []).append(slice_value(row))
    period_values = []
    for instant, values in sorted(periods.items()):
        if group_by == 'year':
            label = f'{instant.year:04d}'
        elif group_by == 'month':
            label = f'{instant.year:04d}-{instant.month:02d}'
        else:
            label = instant.date().isoformat()
        period_values.append({'period': label, **summary(values),
            **({'year': instant.year, 'month': instant.month} if group_by == 'month' else {})})
    client_groups = {}
    for row in client_rows:
        value = slice_value(row)
        client_groups.setdefault(row['currency'], []).append({
            'client_id': row['client_id'], 'client_name': row['client_name'],
            'amount': value['amount'], 'visit_count': value['count'], 'requires_review': value['requires_review']})
    top_clients_by_currency = [{'currency': unit, 'clients': values} for unit, values in client_groups.items()]
    single = overall['by_currency'][0] if len(overall['by_currency']) == 1 else None
    clean = single is not None and not single['requires_review']
    top_clients = [{'client_id': row['client_id'], 'client_name': row['client_name'],
                    'total_revenue': compatibility(row['amount']), 'visit_count': row['visit_count']}
                   for row in client_groups.get(single['currency'], [])] if clean else []
    return {
        'value_basis': 'quoted_service_price', 'currency': overall['currency'],
        'total_revenue': overall['revenue'],
        'average_revenue_per_visit': compatibility(single['average']) if clean else None,
        'by_currency': overall['by_currency'], 'revenue_by_status': statuses,
        'revenue_by_period': period_values, 'top_clients': top_clients,
        'top_clients_by_currency': top_clients_by_currency,
    }


def get_payment_statistics(
    db: Session,
    specialist_id: Optional[int] = None,
    business_id: Optional[int] = None,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Completed visit prices by payment method, per stored visit currency.

    Counts are fine across currencies; totals and percentages only exist inside
    one currency. The old flat money fields stay only for one known currency.
    """
    filters = [Visit.deleted_at.is_(None), Visit.status == 'completed', Visit.price.isnot(None), Visit.price > 0]
    if specialist_id:
        filters.append(Visit.specialist_id == specialist_id)
    if business_id:
        filters.append(Visit.business_id == business_id)
    if date_from:
        filters.append(Visit.date_start >= date_from)
    if date_to:
        filters.append(Visit.date_start <= date_to)
    methods = grouped_quotes(db, [func.coalesce(Visit.payment_method, 'unknown').label('method')], filters)
    per_unit = {}
    for entry in methods:
        for value in entry['by_currency']:
            per_unit.setdefault(value['currency'], {})[entry['key'][0]] = value
    by_currency = []
    for unit in sorted(per_unit, key=currency_order):
        values = per_unit[unit]
        review = any(value['requires_review'] for value in values.values())
        total = None if review else sum((value['amount'] for value in values.values()), Decimal('0.00'))

        def share(value, total=total):
            if total is None or value['amount'] is None:
                return None
            return round(float(value['amount']) / float(total) * 100, 2) if total > 0 else 0.0
        by_currency.append({
            'currency': unit, 'total': total, 'count': sum(value['count'] for value in values.values()),
            'requires_review': review,
            'payments_by_method': {method: {'total': None if review else value['amount'], 'count': value['count'],
                                            'percentage': share(value)} for method, value in values.items()},
        })
    single = by_currency[0] if len(by_currency) == 1 and not by_currency[0]['requires_review'] else None
    payments_by_method_dict = {}
    for entry in methods:
        method = entry['key'][0]
        if single:
            value = single['payments_by_method'][method]
            payments_by_method_dict[method] = {'total': as_float(value['total']), 'count': value['count'],
                                               'percentage': value['percentage']}
        else:
            payments_by_method_dict[method] = {'total': None, 'count': entry['visits'], 'percentage': None}
    empty = {'total': 0.0, 'count': 0, 'percentage': 0.0}
    return {
        'total_payments': as_float(single['total']) if single else (0.0 if not by_currency else None),
        'total_count': sum(entry['visits'] for entry in methods),
        'payments_by_method': payments_by_method_dict,
        'card_payments': payments_by_method_dict.get('card', empty),
        'cash_payments': payments_by_method_dict.get('cash', empty),
        'other_payments': payments_by_method_dict.get('other', empty),
        'currency': single['currency'] if single else None,
        'by_currency': by_currency,
    }


def get_popular_services(
    db: Session,
    specialist_id: Optional[int] = None,
    business_id: Optional[int] = None,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    limit: int = 10,
) -> List[Dict[str, Any]]:
    """Most booked services; their money per stored visit currency.

    visit_count counts every visit (any status, priced or not), like before.
    total_revenue / average_price stay only when the whole list is in one
    known currency, otherwise null and the numbers live in by_currency.
    """
    service_filters = [Visit.deleted_at.is_(None)]
    if specialist_id:
        service_filters.append(Visit.specialist_id == specialist_id)
    if business_id:
        service_filters.append(Visit.business_id == business_id)
    if date_from:
        service_filters.append(Visit.date_start >= date_from)
    if date_to:
        service_filters.append(Visit.date_start <= date_to)
    services = grouped_quotes(db, [Visit.service_id.label('service_id'), Service.name.label('service_name')],
                              service_filters, joins=[(Service, Visit.service_id == Service.id)], limit=limit)
    clean = single_currency([value for entry in services for value in entry['by_currency']])
    result = []
    for entry in services:
        service_id, name = entry['key']
        slices = entry['by_currency']
        if not slices:
            total, average_price = 0.0, None
        elif clean is not None:
            total, average_price = as_float(slices[0]['amount']), as_float(slices[0]['average'])
        else:
            total, average_price = None, None
        result.append({
            'service_id': service_id, 'service_name': name, 'visit_count': entry['visits'],
            'total_revenue': total, 'average_price': average_price,
            'currency': single_currency(slices), 'by_currency': slices,
        })
    return result


def get_client_statistics(
    db: Session,
    owner_id: Optional[int] = None,
    business_id: Optional[int] = None,
) -> Dict[str, Any]:
    base_query = db.query(Client)
    client_filters = []
    if business_id:
        base_query = base_query.join(
            ClientBusinessRelation, 
            Client.id == ClientBusinessRelation.client_id
        ).filter(ClientBusinessRelation.business_id == business_id)
        client_filters.append(
            Client.id.in_(
                db.query(ClientBusinessRelation.client_id)
                .filter(ClientBusinessRelation.business_id == business_id)
            )
        )
    elif owner_id:
        base_query = base_query.filter(Client.owner_id == owner_id)
        client_filters.append(Client.owner_id == owner_id)
    base_query = base_query.filter(Client.deleted_at.is_(None))
    base_query = base_query.filter(Client.archived_at.is_(None))
    client_filters.append(Client.deleted_at.is_(None))
    client_filters.append(Client.archived_at.is_(None))
    total_clients = base_query.count()
    today_start = utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    today_end = today_start + timedelta(days=1)
    new_clients_query = db.query(Client).filter(*client_filters)
    new_clients_query = new_clients_query.filter(
        Client.created_at >= today_start,
        Client.created_at < today_end
    )
    new_clients = new_clients_query.count()
    gender_counts = (
        db.query(Client.gender, func.count(Client.id).label('count'))
        .filter(*client_filters)
        .group_by(Client.gender)
        .all()
    )
    clients_by_gender = {gender or 'unknown': count for gender, count in gender_counts}
    # spending = sum of completed visits (there's no stored spending column), per currency
    spend_filters = [*client_filters, Visit.deleted_at.is_(None), Visit.status == 'completed',
                     Visit.price.isnot(None)]
    if business_id:
        spend_filters.append(Visit.business_id == business_id)
    elif owner_id:
        spend_filters.append(Visit.specialist_id == owner_id)
    spenders = grouped_quotes(db, [Client.id.label('client_id'), Client.full_name.label('client_name')],
                              spend_filters, joins=[(Client, Visit.client_id == Client.id)])
    per_unit = {}
    for entry in spenders:
        for value in entry['by_currency']:
            per_unit.setdefault(value['currency'], []).append((entry, value))
    top_spenders_by_currency, average_spending_by_currency = [], []
    for unit in sorted(per_unit, key=currency_order):
        rows = sorted(per_unit[unit], key=lambda pair: (pair[1]['amount'] is None, -(pair[1]['amount'] or 0),
                                                        pair[0]['key'][0]))
        review = any(value['requires_review'] for _, value in rows)
        amounts = [value['amount'] for _, value in rows]
        average_spending_by_currency.append({
            'currency': unit, 'client_count': len(rows), 'requires_review': review,
            'average': None if review else average(sum(amounts, Decimal('0.00')), len(amounts)),
        })
        top_spenders_by_currency.append({'currency': unit, 'clients': [
            {'client_id': entry['key'][0], 'client_name': entry['key'][1], 'amount': value['amount'],
             'visit_count': value['count'], 'requires_review': value['requires_review']}
            for entry, value in rows[:10]]})
    single = average_spending_by_currency[0] if len(average_spending_by_currency) == 1 else None
    single = single if single and not single['requires_review'] else None
    top_spenders_list = [
        {'client_id': row['client_id'], 'client_name': row['client_name'], 'spending': as_float(row['amount'])}
        for row in top_spenders_by_currency[0]['clients']
    ] if single else []
    avg_spending = single['average'] if single else None
    return {
        'total_clients': total_clients,
        'new_clients': new_clients,
        'clients_by_gender': clients_by_gender,
        'average_spending': as_float(avg_spending) if avg_spending else None,
        'top_spenders': top_spenders_list,
        'currency': single['currency'] if single else None,
        'average_spending_by_currency': average_spending_by_currency,
        'top_spenders_by_currency': top_spenders_by_currency,
    }
