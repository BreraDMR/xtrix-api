from typing import Optional, List

from sqlalchemy.orm import Session

from app.db.models.client_specialist_relation import ClientSpecialistRelation
from app.schemas.client_specialist_relation import ClientSpecialistRelationCreate


def create_relation(
    db: Session, relation_in: ClientSpecialistRelationCreate
) -> ClientSpecialistRelation:
    """Create a new client-specialist relation."""
    # Если business_id не указан, пытаемся получить его из specialist
    business_id = relation_in.business_id
    if business_id is None:
        from app.db.models.user import User
        specialist = db.query(User).filter(User.id == relation_in.specialist_id).first()
        if specialist and hasattr(specialist, 'business_id'):
            business_id = specialist.business_id
    
    # Check if relation already exists (with business_id if provided)
    existing = get_relation(
        db, 
        relation_in.client_id, 
        relation_in.specialist_id,
        business_id
    )
    if existing:
        return existing
    
    # Убеждаемся, что business_id указан
    if business_id is None:
        raise ValueError("business_id is required for ClientSpecialistRelation")
    
    relation_data = relation_in.dict()
    relation_data['business_id'] = business_id
    relation = ClientSpecialistRelation(**relation_data)
    db.add(relation)
    db.commit()
    db.refresh(relation)
    return relation


def get_relation(
    db: Session, client_id: int, specialist_id: int, business_id: Optional[int] = None
) -> Optional[ClientSpecialistRelation]:
    """Get a specific client-specialist relation."""
    query = db.query(ClientSpecialistRelation).filter(
        ClientSpecialistRelation.client_id == client_id,
        ClientSpecialistRelation.specialist_id == specialist_id,
    )
    if business_id is not None:
        query = query.filter(ClientSpecialistRelation.business_id == business_id)
    return query.first()


def get_client_specialists(db: Session, client_id: int) -> List[ClientSpecialistRelation]:
    """Get all specialists linked to a client."""
    return db.query(ClientSpecialistRelation).filter(
        ClientSpecialistRelation.client_id == client_id
    ).all()


def get_specialist_clients(db: Session, specialist_id: int) -> List[ClientSpecialistRelation]:
    """Get all clients linked to a specialist."""
    return db.query(ClientSpecialistRelation).filter(
        ClientSpecialistRelation.specialist_id == specialist_id
    ).all()


def delete_relation(
    db: Session, client_id: int, specialist_id: int
) -> bool:
    """Delete a client-specialist relation."""
    relation = get_relation(db, client_id, specialist_id)
    if not relation:
        return False
    
    db.delete(relation)
    db.commit()
    return True


