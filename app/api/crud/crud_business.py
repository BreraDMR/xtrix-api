from typing import Optional
from sqlalchemy.orm import Session
from app.db.models.business import Business
from app.schemas.business import BusinessCreate, BusinessUpdate
from app.api.core.clock import utcnow
def create_business(db: Session, business_in: BusinessCreate) -> Business:
    business = Business(
        name=business_in.name,
        business_type=business_in.business_type,
        address=business_in.address,
        phone=business_in.phone,
        email=business_in.email,
        description=business_in.description,
        currency=business_in.currency,
        timezone=business_in.timezone,
        booking_horizon_days=business_in.booking_horizon_days,
        booking_lead_time_minutes=business_in.booking_lead_time_minutes,
    )
    db.add(business)
    db.commit()
    db.refresh(business)
    return business
def get_business(db: Session, business_id: int) -> Optional[Business]:
    return db.query(Business).filter(
        Business.id == business_id,
        Business.deleted_at.is_(None)  
    ).first()
def get_businesses(
    db: Session,
    skip: int = 0,
    limit: int = 100
) -> list[Business]:
    return db.query(Business).filter(
        Business.deleted_at.is_(None)
    ).offset(skip).limit(limit).all()
def update_business(
    db: Session,
    business: Business,
    business_update: BusinessUpdate
) -> Business:
    update_data = business_update.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(business, field, value)
    db.add(business)
    db.commit()
    db.refresh(business)
    return business
def delete_business(db: Session, business: Business) -> Business:
    business.deleted_at = utcnow()
    db.add(business)
    db.commit()
    db.refresh(business)
    return business
