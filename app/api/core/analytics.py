from datetime import datetime, timedelta
from decimal import Decimal, localcontext, ROUND_HALF_UP
from math import isfinite
from typing import Dict, Any, List, Optional
from sqlalchemy.orm import Session
from sqlalchemy import func, extract, select, case, and_, cast, Numeric, String, DateTime, Integer, BigInteger, null, literal, union_all
from app.db.models.visit import Visit
from app.db.models.service import Service
from app.db.models.client import Client
from app.db.models.client_business_relation import ClientBusinessRelation
from app.api.core.clock import utcnow
from app.schemas.limits import ISO_4217
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
    currency = case((Visit.currency_snapshot.in_(sorted(ISO_4217)), Visit.currency_snapshot), else_=None)
    # CASE protects the cast and arithmetic from legacy NaN/infinity. Numeric
    # keeps the decimal value PostgreSQL reads, without a double precision SUM.
    price = case((and_(Visit.price.is_not(None), Visit.price >= 0,
                       Visit.price < float('inf')), cast(Visit.price, Numeric())), else_=None)
    cents = case((and_(currency.is_not(None), price == func.trunc(price, 2)), price * 100), else_=None)
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
        return (func.count().label('count'), func.count(quotes.c.cents).label('valid_count'),
                func.sum(quotes.c.cents).label('total_cents'))

    def from_cents(value):
        integer = int(value)
        return Decimal(f'{integer // 100}.{integer % 100:02d}')

    def slice_value(row):
        count = int(row['count'])
        review = row['currency'] is None or row['valid_count'] != count
        amount = None if review else from_cents(row['total_cents'])
        average = None
        if amount is not None:
            with localcontext() as money_context:
                money_context.prec = max(32, len(amount.as_tuple().digits) + 16)
                average = (amount / count).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        return {'currency': row['currency'], 'amount': amount, 'average': average,
                'count': count, 'requires_review': review}

    def compatibility(value):
        if value is None:
            return None
        number = float(value)
        return number if isfinite(number) else None

    def summary(values):
        values = sorted(values, key=lambda row: (row['currency'] is None, row['currency'] or ''))
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
    base_filters = []
    if specialist_id:
        base_filters.append(Visit.specialist_id == specialist_id)
    if business_id:
        base_filters.append(Visit.business_id == business_id)
    if date_from:
        base_filters.append(Visit.date_start >= date_from)
    if date_to:
        base_filters.append(Visit.date_start <= date_to)
    payment_filters = base_filters + [
        Visit.status == 'completed',
        Visit.price.isnot(None),
        Visit.price > 0,
    ]
    total_payments = (
        db.query(func.sum(Visit.price))
        .filter(*payment_filters)
        .scalar() or 0.0
    )
    total_count = (
        db.query(func.count(Visit.id))
        .filter(*payment_filters)
        .scalar() or 0
    )
    payments_by_method = (
        db.query(
            Visit.payment_method,
            func.sum(Visit.price).label('total'),
            func.count(Visit.id).label('count')
        )
        .filter(*payment_filters)
        .group_by(Visit.payment_method)
        .all()
    )
    payments_by_method_dict = {}
    for method, total, count in payments_by_method:
        method_key = method or 'unknown'
        payments_by_method_dict[method_key] = {
            'total': float(total),
            'count': int(count),
            'percentage': round((float(total) / total_payments * 100) if total_payments > 0 else 0.0, 2),
        }
    card_payments = payments_by_method_dict.get('card', {'total': 0.0, 'count': 0, 'percentage': 0.0})
    cash_payments = payments_by_method_dict.get('cash', {'total': 0.0, 'count': 0, 'percentage': 0.0})
    other_payments = payments_by_method_dict.get('other', {'total': 0.0, 'count': 0, 'percentage': 0.0})
    return {
        'total_payments': float(total_payments),
        'total_count': int(total_count),
        'payments_by_method': payments_by_method_dict,
        'card_payments': card_payments,
        'cash_payments': cash_payments,
        'other_payments': other_payments,
    }
def get_popular_services(
    db: Session,
    specialist_id: Optional[int] = None,
    business_id: Optional[int] = None,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    limit: int = 10,
) -> List[Dict[str, Any]]:
    service_filters = []
    if specialist_id:
        service_filters.append(Visit.specialist_id == specialist_id)
    if business_id:
        service_filters.append(Visit.business_id == business_id)
    if date_from:
        service_filters.append(Visit.date_start >= date_from)
    if date_to:
        service_filters.append(Visit.date_start <= date_to)
    q = (
        db.query(
            Visit.service_id,
            Service.name.label('service_name'),
            func.count(Visit.id).label('visit_count'),
            func.sum(Visit.price).label('total_revenue'),
            func.avg(Visit.price).label('avg_price'),
        )
        .join(Service, Visit.service_id == Service.id)
        .filter(*service_filters)
        .group_by(Visit.service_id, Service.name)
        .order_by(func.count(Visit.id).desc())
        .limit(limit)
    )
    results = q.all()
    return [
        {
            'service_id': service_id,
            'service_name': name,
            'visit_count': int(count),
            'total_revenue': float(revenue) if revenue else 0.0,
            'average_price': round(float(avg_price), 2) if avg_price else None,
        }
        for service_id, name, count, revenue, avg_price in results
    ]
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
    # spending = sum of completed visits (there's no stored spending column)
    spending = func.sum(Visit.price).label('spending')
    per_client = (
        db.query(Client.id, Client.full_name, spending)
        .join(Visit, Visit.client_id == Client.id)
        .filter(*client_filters, Visit.status == 'completed', Visit.price.isnot(None))
    )
    if business_id:
        per_client = per_client.filter(Visit.business_id == business_id)
    elif owner_id:
        per_client = per_client.filter(Visit.specialist_id == owner_id)
    per_client = per_client.group_by(Client.id, Client.full_name)
    totals = per_client.subquery()
    avg_spending = db.query(func.avg(totals.c.spending)).scalar()
    top_spenders = per_client.order_by(spending.desc()).limit(10).all()
    top_spenders_list = [
        {
            'client_id': client_id,
            'client_name': name,
            'spending': float(spending) if spending else 0.0,
        }
        for client_id, name, spending in top_spenders
    ]
    return {
        'total_clients': total_clients,
        'new_clients': new_clients,
        'clients_by_gender': clients_by_gender,
        'average_spending': round(float(avg_spending), 2) if avg_spending else None,
        'top_spenders': top_spenders_list,
    }
