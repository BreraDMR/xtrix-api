from typing import Any, Dict, Optional, Tuple
from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.api.core.clock import utcnow
from app.api.crud import crud_subscription
from app.db.models.subscription import Subscription
from app.db.models.user import User


def get_subscription_access_state(db: Session, user_id: int) -> Dict[str, Any]:
    """Evaluate subscription and trial access state for a user per D02 policy.

    Rules:
    1. Expiration does NOT delete or wipe customer or salon data.
    2. Expiration never performs automatic card charges (no card required).
    3. Login, payment/checkout access, and data export remain available.
    4. New paid actions/mutations are restricted when expired.
    """
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        return {
            "has_access": False,
            "is_trial_expired": False,
            "can_export": False,
            "can_manage_billing": False,
            "reason": "user_not_found",
        }

    payer_id = crud_subscription.subscription_payer_id(db, user)
    if payer_id is None:
        return {
            "has_access": False,
            "is_trial_expired": False,
            "can_export": False,
            "can_manage_billing": False,
            "reason": "no_payer",
        }

    sub = crud_subscription.get_active_subscription(db, payer_id)
    now = utcnow()

    if not sub:
        return {
            "has_access": False,
            "is_trial_expired": False,
            "can_export": True,
            "can_manage_billing": True,
            "reason": "no_subscription",
        }

    is_trial_expired = bool(sub.is_trial and sub.trial_end and now > sub.trial_end)
    is_period_expired = bool(sub.current_period_end and now > sub.current_period_end)

    if is_trial_expired or is_period_expired:
        return {
            "has_access": False,
            "is_trial_expired": is_trial_expired,
            "can_export": True,
            "can_manage_billing": True,
            "can_mutate_paid_features": False,
            "reason": "trial_expired" if is_trial_expired else "subscription_expired",
        }

    return {
        "has_access": True,
        "is_trial_expired": False,
        "can_export": True,
        "can_manage_billing": True,
        "can_mutate_paid_features": True,
        "reason": None,
    }


def enforce_subscription_mutation_access(
    db: Session,
    user_id: int,
    feature: Optional[str] = None,
) -> None:
    """Enforce that expired trials cannot perform new paid operations while preserving data."""
    state = get_subscription_access_state(db, user_id)
    if not state.get("has_access"):
        if state.get("is_trial_expired"):
            raise HTTPException(
                status_code=402,
                detail="Your 7-day trial has ended. Subscribe to a plan to continue using this feature. Your data is safely preserved.",
            )
        raise HTTPException(
            status_code=403,
            detail=f"Active subscription required ({state.get('reason')}).",
        )
