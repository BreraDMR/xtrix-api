from datetime import datetime, date
from typing import Dict, List, Any, Optional
from sqlalchemy.orm import Session
from app.db.models.loyalty_program import (
    LoyaltyTransaction,
)
from app.db.models.client_business_relation import ClientBusinessRelation
from app.api.crud import crud_loyalty
def get_loyalty_statistics(
    db: Session,
    business_id: int,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
) -> Dict[str, Any]:
    program = crud_loyalty.get_loyalty_program(db, business_id)
    if not program:
        return {
            "total_participants": 0,
            "total_points_earned": 0.0,
            "total_points_spent": 0.0,
            "total_cashback_earned": 0.0,
            "total_cashback_spent": 0.0,
            "active_points_balance": 0.0,
            "active_cashback_balance": 0.0,
            "points_redemption_rate": 0.0,
            "cashback_redemption_rate": 0.0,
        }
    query = db.query(LoyaltyTransaction).filter(
        LoyaltyTransaction.business_id == business_id,
    )
    if date_from:
        query = query.filter(LoyaltyTransaction.created_at >= date_from)
    if date_to:
        query = query.filter(LoyaltyTransaction.created_at <= date_to)
    transactions = query.all()
    total_points_earned = sum(
        t.points_amount for t in transactions
        if t.transaction_type == 'earned' and t.points_amount > 0
    )
    total_points_spent = abs(sum(
        t.points_amount for t in transactions
        if t.transaction_type == 'spent' and t.points_amount < 0
    ))
    total_cashback_earned = sum(
        t.cashback_amount or 0.0 for t in transactions
        if t.transaction_type == 'earned' and t.cashback_amount and t.cashback_amount > 0
    )
    total_cashback_spent = abs(sum(
        t.cashback_amount or 0.0 for t in transactions
        if t.transaction_type == 'spent' and t.cashback_amount and t.cashback_amount < 0
    ))
    active_relations = db.query(ClientBusinessRelation).filter(
        ClientBusinessRelation.business_id == business_id,
    ).all()
    active_points_balance = sum(r.loyalty_points or 0.0 for r in active_relations)
    active_cashback_balance = sum(r.loyalty_cashback or 0.0 for r in active_relations)
    participant_ids = set(t.client_id for t in transactions)
    total_participants = len(participant_ids)
    return {
        "total_participants": total_participants,
        "total_points_earned": total_points_earned,
        "total_points_spent": total_points_spent,
        "total_cashback_earned": total_cashback_earned,
        "total_cashback_spent": total_cashback_spent,
        "active_points_balance": active_points_balance,
        "active_cashback_balance": active_cashback_balance,
        "points_redemption_rate": (total_points_spent / total_points_earned * 100) if total_points_earned > 0 else 0.0,
        "cashback_redemption_rate": (total_cashback_spent / total_cashback_earned * 100) if total_cashback_earned > 0 else 0.0,
    }
def get_level_distribution(
    db: Session,
    business_id: int,
) -> List[Dict[str, Any]]:
    program = crud_loyalty.get_loyalty_program(db, business_id)
    if not program or not program.use_levels:
        return []
    levels = crud_loyalty.list_loyalty_levels(db, program_id=program.id)
    if not levels:
        return []
    distribution = []
    for level in levels:
        count = db.query(ClientBusinessRelation).filter(
            ClientBusinessRelation.business_id == business_id,
            ClientBusinessRelation.loyalty_level_id == level.id,
        ).count()
        distribution.append({
            "level_id": level.id,
            "level_name": level.name,
            "level_number": level.level_number,
            "client_count": count,
            "min_spending": level.min_spending,
            "color": level.color,
        })
    no_level_count = db.query(ClientBusinessRelation).filter(
        ClientBusinessRelation.business_id == business_id,
        ClientBusinessRelation.loyalty_level_id.is_(None),
    ).count()
    if no_level_count > 0:
        distribution.append({
            "level_id": None,
            "level_name": "No Level",
            "level_number": 0,
            "client_count": no_level_count,
            "min_spending": 0.0,
            "color": None,
        })
    return sorted(distribution, key=lambda x: x["level_number"])
def get_top_clients_by_points(
    db: Session,
    business_id: int,
    limit: int = 10,
) -> List[Dict[str, Any]]:
    relations = db.query(ClientBusinessRelation).filter(
        ClientBusinessRelation.business_id == business_id,
    ).order_by(
        ClientBusinessRelation.loyalty_points.desc(),
        ClientBusinessRelation.loyalty_cashback.desc(),
    ).limit(limit).all()
    result = []
    for relation in relations:
        if (relation.loyalty_points and relation.loyalty_points > 0) or (relation.loyalty_cashback and relation.loyalty_cashback > 0):
            from app.db.models.client import Client
            client = db.query(Client).filter(Client.id == relation.client_id).first()
            if client:
                result.append({
                    "client_id": relation.client_id,
                    "client_name": client.full_name,
                    "points": relation.loyalty_points or 0.0,
                    "cashback": relation.loyalty_cashback or 0.0,
                    "level_id": relation.loyalty_level_id,
                })
    return result
def get_promotion_effectiveness(
    db: Session,
    business_id: int,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
) -> List[Dict[str, Any]]:
    program = crud_loyalty.get_loyalty_program(db, business_id)
    if not program:
        return []
    from app.db.models.loyalty_program import LoyaltyPromotion
    query = db.query(LoyaltyPromotion).filter(
        LoyaltyPromotion.program_id == program.id,
    )
    if date_from:
        query = query.filter(LoyaltyPromotion.start_date >= date_from.date() if isinstance(date_from, datetime) else date_from)
    if date_to:
        query = query.filter(LoyaltyPromotion.end_date <= date_to.date() if isinstance(date_to, datetime) else date_to)
    promotions = query.all()
    result = []
    for promo in promotions:
        tx_query = db.query(LoyaltyTransaction).filter(
            LoyaltyTransaction.business_id == business_id,
            LoyaltyTransaction.promotion_id == promo.id,
        )
        if date_from:
            tx_query = tx_query.filter(LoyaltyTransaction.created_at >= date_from)
        if date_to:
            tx_query = tx_query.filter(LoyaltyTransaction.created_at <= date_to)
        transactions = tx_query.all()
        total_points = sum(t.points_amount for t in transactions if t.points_amount > 0)
        total_cashback = sum(t.cashback_amount or 0.0 for t in transactions if t.cashback_amount and t.cashback_amount > 0)
        unique_clients = len(set(t.client_id for t in transactions))
        result.append({
            "promotion_id": promo.id,
            "promotion_name": promo.name,
            "promotion_type": promo.promotion_type,
            "start_date": promo.start_date.isoformat() if isinstance(promo.start_date, date) else promo.start_date,
            "end_date": promo.end_date.isoformat() if isinstance(promo.end_date, date) else promo.end_date,
            "is_active": promo.is_active,
            "usage_count": len(transactions),
            "unique_clients": unique_clients,
            "total_points_awarded": total_points,
            "total_cashback_awarded": total_cashback,
        })
    return sorted(result, key=lambda x: x["usage_count"], reverse=True)
def get_points_usage_trends(
    db: Session,
    business_id: int,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    group_by: str = "day",  
) -> List[Dict[str, Any]]:
    query = db.query(LoyaltyTransaction).filter(
        LoyaltyTransaction.business_id == business_id,
    )
    if date_from:
        query = query.filter(LoyaltyTransaction.created_at >= date_from)
    if date_to:
        query = query.filter(LoyaltyTransaction.created_at <= date_to)
    transactions = query.order_by(LoyaltyTransaction.created_at).all()
    trends = {}
    for tx in transactions:
        tx_date = tx.created_at
        if group_by == "day":
            key = tx_date.date().isoformat()
        elif group_by == "week":
            days_since_monday = tx_date.weekday()
            week_start = tx_date.date() - datetime.timedelta(days=days_since_monday)
            key = week_start.isoformat()
        else:  
            key = f"{tx_date.year}-{tx_date.month:02d}"
        if key not in trends:
            trends[key] = {
                "period": key,
                "points_earned": 0.0,
                "points_spent": 0.0,
                "cashback_earned": 0.0,
                "cashback_spent": 0.0,
                "transaction_count": 0,
            }
        if tx.transaction_type == 'earned':
            trends[key]["points_earned"] += tx.points_amount if tx.points_amount > 0 else 0.0
            trends[key]["cashback_earned"] += tx.cashback_amount if tx.cashback_amount and tx.cashback_amount > 0 else 0.0
        elif tx.transaction_type == 'spent':
            trends[key]["points_spent"] += abs(tx.points_amount) if tx.points_amount < 0 else 0.0
            trends[key]["cashback_spent"] += abs(tx.cashback_amount) if tx.cashback_amount and tx.cashback_amount < 0 else 0.0
        trends[key]["transaction_count"] += 1
    return sorted(trends.values(), key=lambda x: x["period"])
