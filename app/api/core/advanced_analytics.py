from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional
from sqlalchemy.orm import Session
from sqlalchemy import func, extract
from collections import defaultdict
from app.db.models.visit import Visit
from app.db.models.service import Service
from app.db.models.user import User
from app.api.core.clock import utcnow
def get_retention_metrics(
    db: Session,
    business_id: Optional[int] = None,
    specialist_id: Optional[int] = None,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
) -> Dict[str, Any]:
    filters = []
    if business_id:
        filters.append(Visit.business_id == business_id)
    if specialist_id:
        filters.append(Visit.specialist_id == specialist_id)
    if date_from:
        filters.append(Visit.date_start >= date_from)
    if date_to:
        filters.append(Visit.date_start <= date_to)
    visits_query = db.query(
        Visit.client_id,
        Visit.date_start,
        Visit.status
    ).filter(*filters if filters else [True]).filter(Visit.status == 'completed').all()
    if not visits_query:
        return {
            'retention_7_days': 0.0,
            'retention_30_days': 0.0,
            'retention_90_days': 0.0,
            'repeat_rate': 0.0,
            'average_days_between_visits': None,
            'total_clients': 0,
            'repeat_clients': 0,
        }
    client_visits = defaultdict(list)
    for client_id, date_start, status in visits_query:
        client_visits[client_id].append(date_start)
    retention_periods = [7, 30, 90]
    retention_metrics = {}
    for period in retention_periods:
        retained_count = 0
        eligible_count = 0
        for client_id, visit_dates in client_visits.items():
            if len(visit_dates) < 2:
                continue
            sorted_dates = sorted(visit_dates)
            first_visit = sorted_dates[0]
            cutoff_date = first_visit + timedelta(days=period)
            has_return = any(d > first_visit and d <= cutoff_date for d in sorted_dates[1:])
            if first_visit <= (date_to or utcnow()) - timedelta(days=period):
                eligible_count += 1
                if has_return:
                    retained_count += 1
        retention_rate = (retained_count / eligible_count * 100) if eligible_count > 0 else 0.0
        retention_metrics[f'retention_{period}_days'] = round(retention_rate, 2)
    total_clients = len(client_visits)
    repeat_clients = sum(1 for dates in client_visits.values() if len(dates) >= 2)
    repeat_rate = (repeat_clients / total_clients * 100) if total_clients > 0 else 0.0
    all_intervals = []
    for client_id, visit_dates in client_visits.items():
        if len(visit_dates) < 2:
            continue
        sorted_dates = sorted(visit_dates)
        for i in range(1, len(sorted_dates)):
            interval = (sorted_dates[i] - sorted_dates[i-1]).days
            if interval > 0:
                all_intervals.append(interval)
    avg_days_between = sum(all_intervals) / len(all_intervals) if all_intervals else None
    return {
        **retention_metrics,
        'repeat_rate': round(repeat_rate, 2),
        'average_days_between_visits': round(avg_days_between, 2) if avg_days_between else None,
        'total_clients': total_clients,
        'repeat_clients': repeat_clients,
    }
def get_client_cohorts(
    db: Session,
    business_id: Optional[int] = None,
    specialist_id: Optional[int] = None,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
) -> Dict[str, Any]:
    filters = []
    if business_id:
        filters.append(Visit.business_id == business_id)
    if specialist_id:
        filters.append(Visit.specialist_id == specialist_id)
    if date_from:
        filters.append(Visit.date_start >= date_from)
    if date_to:
        filters.append(Visit.date_start <= date_to)
    visits_query = db.query(
        Visit.client_id,
        Visit.date_start,
        Visit.status
    ).filter(*filters if filters else [True]).filter(Visit.status == 'completed').all()
    client_cohorts = {}
    client_visits = defaultdict(list)
    for client_id, date_start, status in visits_query:
        client_visits[client_id].append(date_start)
        if client_id not in client_cohorts:
            client_cohorts[client_id] = date_start
        else:
            if date_start < client_cohorts[client_id]:
                client_cohorts[client_id] = date_start
    cohort_groups = defaultdict(list)
    for client_id, first_visit in client_cohorts.items():
        cohort_month = first_visit.strftime('%Y-%m')
        cohort_groups[cohort_month].append(client_id)
    cohort_table = []
    for cohort_month, client_ids in sorted(cohort_groups.items()):
        cohort_first_date = datetime.strptime(cohort_month + '-01', '%Y-%m-%d')
        month_retention = {}
        for month_offset in range(12):  
            target_month_start = cohort_first_date + timedelta(days=30 * month_offset)
            target_month_end = target_month_start + timedelta(days=30)
            returned_count = 0
            for client_id in client_ids:
                visit_dates = sorted(client_visits[client_id])
                first_visit = visit_dates[0]
                has_return = any(
                    d > first_visit and 
                    target_month_start <= d < target_month_end
                    for d in visit_dates[1:]
                )
                if has_return:
                    returned_count += 1
            total = len(client_ids)
            retention_pct = (returned_count / total * 100) if total > 0 else 0.0
            month_retention[f'month_{month_offset}'] = round(retention_pct, 2)
        cohort_table.append({
            'cohort_month': cohort_month,
            'total_clients': len(client_ids),
            **month_retention,
        })
    return {
        'cohort_table': cohort_table,
        'total_cohorts': len(cohort_table),
    }
def get_trends_analysis(
    db: Session,
    business_id: Optional[int] = None,
    specialist_id: Optional[int] = None,
    metric: str = 'revenue',  
    comparison_type: str = 'MoM',  
) -> Dict[str, Any]:
    now = utcnow()
    if comparison_type == 'MoM':
        current_start = datetime(now.year, now.month, 1)
        current_end = now
        previous_start = (current_start - timedelta(days=32)).replace(day=1)
        previous_end = current_start - timedelta(seconds=1)
        period_label = 'month'
    elif comparison_type == 'WoW':
        current_start = now - timedelta(days=now.weekday() + 7)
        current_end = now
        previous_start = current_start - timedelta(days=7)
        previous_end = current_start - timedelta(seconds=1)
        period_label = 'week'
    elif comparison_type == 'YoY':
        current_start = datetime(now.year, 1, 1)
        current_end = now
        previous_start = datetime(now.year - 1, 1, 1)
        previous_end = datetime(now.year - 1, 12, 31, 23, 59, 59)
        period_label = 'year'
    else:
        raise ValueError(f"Invalid comparison_type: {comparison_type}")
    filters_base = []
    if business_id:
        filters_base.append(Visit.business_id == business_id)
    if specialist_id:
        filters_base.append(Visit.specialist_id == specialist_id)
    if metric == 'revenue':
        current_filters = filters_base + [
            Visit.date_start >= current_start,
            Visit.date_start <= current_end,
            Visit.price.isnot(None),
            Visit.status == 'completed',
        ]
        previous_filters = filters_base + [
            Visit.date_start >= previous_start,
            Visit.date_start <= previous_end,
            Visit.price.isnot(None),
            Visit.status == 'completed',
        ]
        current_value = db.query(func.sum(Visit.price)).filter(*current_filters).scalar() or 0.0
        previous_value = db.query(func.sum(Visit.price)).filter(*previous_filters).scalar() or 0.0
    elif metric == 'clients':
        current_filters = filters_base + [
            Visit.date_start >= current_start,
            Visit.date_start <= current_end,
            Visit.status == 'completed',
        ]
        previous_filters = filters_base + [
            Visit.date_start >= previous_start,
            Visit.date_start <= previous_end,
            Visit.status == 'completed',
        ]
        current_value = float(db.query(func.count(func.distinct(Visit.client_id))).filter(*current_filters).scalar() or 0)
        previous_value = float(db.query(func.count(func.distinct(Visit.client_id))).filter(*previous_filters).scalar() or 0)
    elif metric == 'visits':
        current_filters = filters_base + [
            Visit.date_start >= current_start,
            Visit.date_start <= current_end,
            Visit.status == 'completed',
        ]
        previous_filters = filters_base + [
            Visit.date_start >= previous_start,
            Visit.date_start <= previous_end,
            Visit.status == 'completed',
        ]
        current_value = float(db.query(func.count(Visit.id)).filter(*current_filters).scalar() or 0)
        previous_value = float(db.query(func.count(Visit.id)).filter(*previous_filters).scalar() or 0)
    elif metric == 'average_check':
        current_filters = filters_base + [
            Visit.date_start >= current_start,
            Visit.date_start <= current_end,
            Visit.price.isnot(None),
            Visit.status == 'completed',
        ]
        previous_filters = filters_base + [
            Visit.date_start >= previous_start,
            Visit.date_start <= previous_end,
            Visit.price.isnot(None),
            Visit.status == 'completed',
        ]
        current_sum = db.query(func.sum(Visit.price)).filter(*current_filters).scalar() or 0.0
        current_count = db.query(func.count(Visit.id)).filter(*current_filters).scalar() or 0
        previous_sum = db.query(func.sum(Visit.price)).filter(*previous_filters).scalar() or 0.0
        previous_count = db.query(func.count(Visit.id)).filter(*previous_filters).scalar() or 0
        current_value = current_sum / current_count if current_count > 0 else 0.0
        previous_value = previous_sum / previous_count if previous_count > 0 else 0.0
    elif metric == 'load':
        current_filters = filters_base + [
            Visit.date_start >= current_start,
            Visit.date_start <= current_end,
            Visit.status == 'completed',
        ]
        previous_filters = filters_base + [
            Visit.date_start >= previous_start,
            Visit.date_start <= previous_end,
            Visit.status == 'completed',
        ]
        current_count = db.query(func.count(Visit.id)).filter(*current_filters).scalar() or 0
        previous_count = db.query(func.count(Visit.id)).filter(*previous_filters).scalar() or 0
        current_days = (current_end - current_start).days or 1
        previous_days = (previous_end - previous_start).days or 1
        current_value = current_count / current_days
        previous_value = previous_count / previous_days
    else:
        raise ValueError(f"Invalid metric: {metric}")
    change = current_value - previous_value
    change_percent = (change / previous_value * 100) if previous_value > 0 else (100.0 if current_value > 0 else 0.0)
    trend_direction = 'up' if change > 0 else 'down' if change < 0 else 'stable'
    return {
        'metric': metric,
        'comparison_type': comparison_type,
        'period_label': period_label,
        'current_value': round(current_value, 2),
        'previous_value': round(previous_value, 2),
        'change': round(change, 2),
        'change_percent': round(change_percent, 2),
        'trend_direction': trend_direction,
    }
def get_forecast(
    db: Session,
    business_id: Optional[int] = None,
    specialist_id: Optional[int] = None,
    forecast_type: str = 'revenue',  
    periods: int = 1,  
) -> Dict[str, Any]:
    now = utcnow()
    start_date = now - timedelta(days=180)
    filters = []
    if business_id:
        filters.append(Visit.business_id == business_id)
    if specialist_id:
        filters.append(Visit.specialist_id == specialist_id)
    filters.append(Visit.date_start >= start_date)
    filters.append(Visit.status == 'completed')
    if forecast_type == 'revenue':
        filters.append(Visit.price.isnot(None))
        monthly_data = db.query(
            extract('year', Visit.date_start).label('year'),
            extract('month', Visit.date_start).label('month'),
            func.sum(Visit.price).label('value')
        ).filter(*filters).group_by('year', 'month').order_by('year', 'month').all()
    elif forecast_type == 'visits':
        monthly_data = db.query(
            extract('year', Visit.date_start).label('year'),
            extract('month', Visit.date_start).label('month'),
            func.count(Visit.id).label('value')
        ).filter(*filters).group_by('year', 'month').order_by('year', 'month').all()
    else:
        raise ValueError(f"Invalid forecast_type: {forecast_type}")
    if not monthly_data:
        return {
            'forecast_type': forecast_type,
            'periods': periods,
            'forecast': [],
            'historical_average': 0.0,
            'last_value': 0.0,
        }
    values = [float(value) for _, _, value in monthly_data]
    if len(values) >= 3:
        forecast_value = sum(values[-3:]) / 3
    else:
        forecast_value = sum(values) / len(values) if values else 0.0
    forecast = []
    for i in range(periods):
        forecast.append({
            'period': (now + timedelta(days=30 * (i + 1))).strftime('%Y-%m'),
            'forecasted_value': round(forecast_value, 2),
        })
    historical_avg = sum(values) / len(values) if values else 0.0
    return {
        'forecast_type': forecast_type,
        'periods': periods,
        'forecast': forecast,
        'historical_average': round(historical_avg, 2),
        'last_value': round(values[-1], 2) if values else 0.0,
    }
def get_service_and_specialist_analytics(
    db: Session,
    business_id: Optional[int] = None,
    specialist_id: Optional[int] = None,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
) -> Dict[str, Any]:
    filters = []
    if business_id:
        filters.append(Visit.business_id == business_id)
    if specialist_id:
        filters.append(Visit.specialist_id == specialist_id)
    if date_from:
        filters.append(Visit.date_start >= date_from)
    if date_to:
        filters.append(Visit.date_start <= date_to)
    filters.append(Visit.status == 'completed')
    service_stats = db.query(
        Visit.service_id,
        Service.name.label('service_name'),
        func.count(Visit.id).label('visit_count'),
        func.sum(Visit.price).label('total_revenue'),
        func.avg(Visit.price).label('avg_price'),
        func.count(func.distinct(Visit.client_id)).label('unique_clients'),
    ).join(Service, Visit.service_id == Service.id).filter(*filters).group_by(
        Visit.service_id, Service.name
    ).all()
    services_analytics = []
    for service_id, name, visit_count, revenue, avg_price, unique_clients in service_stats:
        ltv = (revenue or 0.0) / (unique_clients or 1)
        services_analytics.append({
            'service_id': service_id,
            'service_name': name,
            'visit_count': int(visit_count),
            'total_revenue': float(revenue or 0.0),
            'average_price': round(float(avg_price or 0.0), 2),
            'unique_clients': int(unique_clients),
            'ltv_per_client': round(ltv, 2),
            'visits_per_client': round(visit_count / (unique_clients or 1), 2),
        })
    specialist_stats = db.query(
        Visit.specialist_id,
        User.full_name.label('specialist_name'),
        func.count(Visit.id).label('visit_count'),
        func.sum(Visit.price).label('total_revenue'),
        func.avg(Visit.price).label('avg_check'),
        func.count(func.distinct(Visit.client_id)).label('unique_clients'),
    ).join(User, Visit.specialist_id == User.id).filter(*filters).group_by(
        Visit.specialist_id, User.full_name
    ).all()
    specialists_analytics = []
    for spec_id, name, visit_count, revenue, avg_check, unique_clients in specialist_stats:
        avg_revenue_per_client = (revenue or 0.0) / (unique_clients or 1)
        specialists_analytics.append({
            'specialist_id': spec_id,
            'specialist_name': name or f"Specialist {spec_id}",
            'visit_count': int(visit_count),
            'total_revenue': float(revenue or 0.0),
            'average_check': round(float(avg_check or 0.0), 2),
            'unique_clients': int(unique_clients),
            'revenue_per_client': round(avg_revenue_per_client, 2),
        })
    return {
        'services': services_analytics,
        'specialists': specialists_analytics,
    }
def get_smart_insights(
    db: Session,
    business_id: Optional[int] = None,
    specialist_id: Optional[int] = None,
) -> List[Dict[str, Any]]:
    insights = []
    now = utcnow()
    filters = []
    if business_id:
        filters.append(Visit.business_id == business_id)
    if specialist_id:
        filters.append(Visit.specialist_id == specialist_id)
    retention_metrics = get_retention_metrics(db, business_id=business_id, specialist_id=specialist_id)
    if retention_metrics['retention_30_days'] < 30:
        insights.append({
            'type': 'warning',
            'title': 'Low Retention Rate',
            'message': f"30-day retention is {retention_metrics['retention_30_days']}%, which is below optimal levels.",
            'priority': 'high',
        })
    services_analytics = get_service_and_specialist_analytics(db, business_id=business_id, specialist_id=specialist_id)
    if services_analytics['services']:
        top_service = max(services_analytics['services'], key=lambda x: x['visits_per_client'])
        insights.append({
            'type': 'info',
            'title': 'Top Service by Client Return',
            'message': f"Service '{top_service['service_name']}' has the highest client return rate ({top_service['visits_per_client']:.1f} visits per client).",
            'priority': 'medium',
        })
    revenue_trend = get_trends_analysis(db, business_id=business_id, specialist_id=specialist_id, metric='revenue', comparison_type='MoM')
    if revenue_trend['trend_direction'] == 'down' and abs(revenue_trend['change_percent']) > 5:
        insights.append({
            'type': 'warning',
            'title': 'Revenue Decline',
            'message': f"Revenue decreased by {abs(revenue_trend['change_percent']):.1f}% compared to last month.",
            'priority': 'high',
        })
    elif revenue_trend['trend_direction'] == 'up' and revenue_trend['change_percent'] > 10:
        insights.append({
            'type': 'success',
            'title': 'Revenue Growth',
            'message': f"Revenue increased by {revenue_trend['change_percent']:.1f}% compared to last month!",
            'priority': 'medium',
        })
    load_by_day = db.query(
        extract('dow', Visit.date_start).label('day_of_week'),
        func.count(Visit.id).label('visit_count')
    ).filter(*filters).filter(Visit.status == 'completed').group_by('day_of_week').all()
    if load_by_day:
        day_counts = {day: count for day, count in load_by_day}
        max_day = max(day_counts.items(), key=lambda x: x[1])
        min_day = min(day_counts.items(), key=lambda x: x[1])
        day_names = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday']
        if max_day[1] > min_day[1] * 1.5:
            insights.append({
                'type': 'info',
                'title': 'Load Distribution',
                'message': f"{day_names[max_day[0]]}s are busiest ({max_day[1]} visits), while {day_names[min_day[0]]}s are quietest ({min_day[1]} visits).",
                'priority': 'low',
            })
    return insights
def get_anonymous_benchmarks(
    db: Session,
    business_type: Optional[str] = None,
    min_businesses: int = 10,
) -> Dict[str, Any]:
    from app.db.models.business import Business
    business_filters = [Business.deleted_at.is_(None)]
    if business_type:
        business_filters.append(Business.business_type == business_type)
    eligible_businesses = db.query(func.count(Business.id)).filter(*business_filters).scalar() or 0
    if eligible_businesses < min_businesses:
        return {
            'available': False,
            'reason': f'Not enough businesses (need at least {min_businesses}, have {eligible_businesses})',
        }
    business_ids = [b[0] for b in db.query(Business.id).filter(*business_filters).all()]
    visit_filters = [
        Visit.business_id.in_(business_ids),
        Visit.status == 'completed',
        Visit.price.isnot(None),
    ]
    avg_check = db.query(func.avg(Visit.price)).filter(*visit_filters).scalar() or 0.0
    total_visits = db.query(func.count(Visit.id)).filter(*visit_filters).scalar() or 0
    total_clients = db.query(func.count(func.distinct(Visit.client_id))).filter(*visit_filters).scalar() or 0
    avg_visits_per_client = total_visits / total_clients if total_clients > 0 else 0.0
    client_visit_counts = db.query(
        Visit.client_id,
        func.count(Visit.id).label('visit_count')
    ).filter(*visit_filters).group_by(Visit.client_id).all()
    repeat_clients = sum(1 for _, count in client_visit_counts if count >= 2)
    repeat_rate = (repeat_clients / total_clients * 100) if total_clients > 0 else 0.0
    first_visit = db.query(func.min(Visit.date_start)).filter(*visit_filters).scalar()
    last_visit = db.query(func.max(Visit.date_start)).filter(*visit_filters).scalar()
    if first_visit and last_visit:
        days_span = (last_visit - first_visit).days or 1
        avg_load = total_visits / days_span
    else:
        avg_load = 0.0
    return {
        'available': True,
        'business_type': business_type,
        'business_count': eligible_businesses,
        'average_check': round(float(avg_check), 2),
        'average_visits_per_client': round(avg_visits_per_client, 2),
        'average_retention_rate': round(repeat_rate, 2),
        'average_daily_load': round(avg_load, 2),
    }
