"""Analytics and reporting utilities."""
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional, Tuple
from sqlalchemy.orm import Session
from sqlalchemy import func, and_, extract, case

from app.db.models.visit import Visit
from app.db.models.service import Service
from app.db.models.client import Client
from app.db.models.client_business_relation import ClientBusinessRelation


def get_visit_statistics(
    db: Session,
    specialist_id: Optional[int] = None,
    client_id: Optional[int] = None,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Get comprehensive visit statistics."""
    q = db.query(Visit)
    
    # Apply filters
    if specialist_id:
        q = q.filter(Visit.specialist_id == specialist_id)
    if client_id:
        q = q.filter(Visit.client_id == client_id)
    if date_from:
        q = q.filter(Visit.date_start >= date_from)
    if date_to:
        q = q.filter(Visit.date_start <= date_to)
    
    # Total visits
    total_visits = q.count()
    
    # Visits by status
    status_counts = (
        db.query(Visit.status, func.count(Visit.id).label('count'))
        .filter(*[
            Visit.specialist_id == specialist_id if specialist_id else True,
            Visit.client_id == client_id if client_id else True,
            Visit.date_start >= date_from if date_from else True,
            Visit.date_start <= date_to if date_to else True,
        ])
        .group_by(Visit.status)
        .all()
    )
    
    visits_by_status = {status: count for status, count in status_counts}
    
    # Base filters for monthly visits and duration calculations
    base_filters = []
    if specialist_id:
        base_filters.append(Visit.specialist_id == specialist_id)
    if client_id:
        base_filters.append(Visit.client_id == client_id)
    if date_from:
        base_filters.append(Visit.date_start >= date_from)
    if date_to:
        base_filters.append(Visit.date_start <= date_to)
    
    # Visits by month
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
    
    # Average visit duration
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
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    group_by: str = 'month',  # 'day', 'week', 'month', 'year'
) -> Dict[str, Any]:
    """Get financial report with revenue statistics."""
    q = db.query(Visit).filter(Visit.price.isnot(None))
    
    # Apply filters
    if specialist_id:
        q = q.filter(Visit.specialist_id == specialist_id)
    if date_from:
        q = q.filter(Visit.date_start >= date_from)
    if date_to:
        q = q.filter(Visit.date_start <= date_to)
    
    # Total revenue
    total_revenue = q.with_entities(func.sum(Visit.price)).scalar() or 0.0
    
    # Revenue by status
    revenue_by_status = (
        db.query(
            Visit.status,
            func.sum(Visit.price).label('revenue'),
            func.count(Visit.id).label('count')
        )
        .filter(*[
            Visit.price.isnot(None),
            Visit.specialist_id == specialist_id if specialist_id else True,
            Visit.date_start >= date_from if date_from else True,
            Visit.date_start <= date_to if date_to else True,
        ])
        .group_by(Visit.status)
        .all()
    )
    
    revenue_by_status_dict = {
        status: {
            'revenue': float(revenue),
            'count': int(count),
        }
        for status, revenue, count in revenue_by_status
    }
    
    # Revenue by period
    if group_by == 'month':
        period_query = (
            db.query(
                extract('year', Visit.date_start).label('year'),
                extract('month', Visit.date_start).label('month'),
                func.sum(Visit.price).label('revenue'),
                func.count(Visit.id).label('count')
            )
            .filter(*revenue_filters)
            .group_by('year', 'month')
            .order_by('year', 'month')
            .all()
        )
        
        revenue_by_period = [
            {
                'period': f"{int(year)}-{int(month):02d}",
                'year': int(year),
                'month': int(month),
                'revenue': float(revenue),
                'count': int(count),
            }
            for year, month, revenue, count in period_query
        ]
    elif group_by == 'day':
        period_query = (
            db.query(
                func.date(Visit.date_start).label('date'),
                func.sum(Visit.price).label('revenue'),
                func.count(Visit.id).label('count')
            )
            .filter(*revenue_filters)
            .group_by('date')
            .order_by('date')
            .all()
        )
        
        revenue_by_period = [
            {
                'period': date.isoformat(),
                'revenue': float(revenue),
                'count': int(count),
            }
            for date, revenue, count in period_query
        ]
    else:
        revenue_by_period = []
    
    # Average revenue per visit
    avg_revenue_per_visit = (
        db.query(func.avg(Visit.price))
        .filter(*revenue_filters)
        .scalar()
    )
    
    # Top clients by revenue
    top_clients = (
        db.query(
            Visit.client_id,
            Client.full_name,
            func.sum(Visit.price).label('total_revenue'),
            func.count(Visit.id).label('visit_count')
        )
        .join(Client, Visit.client_id == Client.id)
        .filter(*revenue_filters)
        .group_by(Visit.client_id, Client.full_name)
        .order_by(func.sum(Visit.price).desc())
        .limit(10)
        .all()
    )
    
    top_clients_list = [
        {
            'client_id': client_id,
            'client_name': name,
            'total_revenue': float(revenue),
            'visit_count': int(count),
        }
        for client_id, name, revenue, count in top_clients
    ]
    
    return {
        'total_revenue': float(total_revenue),
        'average_revenue_per_visit': round(float(avg_revenue_per_visit), 2) if avg_revenue_per_visit else None,
        'revenue_by_status': revenue_by_status_dict,
        'revenue_by_period': revenue_by_period,
        'top_clients': top_clients_list,
    }


def get_payment_statistics(
    db: Session,
    specialist_id: Optional[int] = None,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Get payment statistics grouped by payment method."""
    base_filters = []
    if specialist_id:
        base_filters.append(Visit.specialist_id == specialist_id)
    if date_from:
        base_filters.append(Visit.date_start >= date_from)
    if date_to:
        base_filters.append(Visit.date_start <= date_to)
    
    # Only completed visits with price
    payment_filters = base_filters + [
        Visit.status == 'completed',
        Visit.price.isnot(None),
        Visit.price > 0,
    ]
    
    # Total payments
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
    
    # Payments by method (card, cash, other)
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
    
    # Получаем данные по каждому способу оплаты
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
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    limit: int = 10,
) -> List[Dict[str, Any]]:
    """Get most popular services by visit count and revenue."""
    service_filters = []
    if specialist_id:
        service_filters.append(Visit.specialist_id == specialist_id)
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
    """Get client statistics."""
    
    # Build base query with filters
    base_query = db.query(Client)
    client_filters = []
    
    # Filter by business_id using ClientBusinessRelation
    if business_id:
        # Join with ClientBusinessRelation to filter by business_id
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
        # Legacy filter by owner_id
        base_query = base_query.filter(Client.owner_id == owner_id)
        client_filters.append(Client.owner_id == owner_id)
    
    # Filter out deleted and archived clients
    base_query = base_query.filter(Client.deleted_at.is_(None))
    base_query = base_query.filter(Client.archived_at.is_(None))
    client_filters.append(Client.deleted_at.is_(None))
    client_filters.append(Client.archived_at.is_(None))
    
    total_clients = base_query.count()
    
    # New clients (created today)
    today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    today_end = today_start + timedelta(days=1)
    
    new_clients_query = db.query(Client).filter(*client_filters)
    new_clients_query = new_clients_query.filter(
        Client.created_at >= today_start,
        Client.created_at < today_end
    )
    new_clients = new_clients_query.count()
    
    # Clients by gender
    gender_counts = (
        db.query(Client.gender, func.count(Client.id).label('count'))
        .filter(*client_filters)
        .group_by(Client.gender)
        .all()
    )
    
    clients_by_gender = {gender or 'unknown': count for gender, count in gender_counts}
    
    # Average spending - need to check if using business_id, get from ClientBusinessRelation
    if business_id:
        avg_spending = (
            db.query(func.avg(ClientBusinessRelation.spending))
            .filter(ClientBusinessRelation.business_id == business_id)
            .scalar()
        )
        
        # Top spenders from ClientBusinessRelation
        top_spenders = (
            db.query(
                Client.id,
                Client.full_name,
                ClientBusinessRelation.spending,
            )
            .join(ClientBusinessRelation, Client.id == ClientBusinessRelation.client_id)
            .filter(
                ClientBusinessRelation.business_id == business_id,
                Client.deleted_at.is_(None),
                Client.archived_at.is_(None),
            )
            .order_by(ClientBusinessRelation.spending.desc())
            .limit(10)
            .all()
        )
    else:
        # Legacy: use Client.spending directly
        avg_spending = (
            db.query(func.avg(Client.spending))
            .filter(*client_filters)
            .scalar()
        )
        
        top_spenders = (
            db.query(
                Client.id,
                Client.full_name,
                Client.spending,
            )
            .filter(*client_filters)
            .order_by(Client.spending.desc())
            .limit(10)
            .all()
        )
    
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

