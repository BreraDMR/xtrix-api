"""A stored quote from one exact CRM scope and one SQL statement."""
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.core.visit_price_quote import StoredVisitPrice, StoredVisitPriceItem
from app.db.models.visit import Visit
from app.db.models.visit_item import VisitItem


def read_stored_price(db: Session, authorized_visit: Visit) -> StoredVisitPrice | None:
    """The caller already checked CRM scope and financial visibility.

    Matching the original client/master prevents a retargeted hint from reading
    another assignment. No cached catalog, default currency or contact is read.
    """
    with db.no_autoflush:
        scoped = select(Visit.id, Visit.currency_snapshot, Visit.price, Visit.service_id,
            Visit.specialist_id).where(Visit.id == authorized_visit.id,
                Visit.business_id == authorized_visit.business_id,
                Visit.client_id == authorized_visit.client_id,
                Visit.specialist_id == authorized_visit.specialist_id,
                Visit.deleted_at.is_(None)).subquery()
        rows = db.execute(select(scoped, VisitItem.id.label('item_id'), VisitItem.position,
            VisitItem.service_id.label('item_service_id'), VisitItem.specialist_id.label('item_master_id'),
            VisitItem.price.label('item_price')).select_from(scoped).outerjoin(
                VisitItem, VisitItem.visit_id == scoped.c.id).order_by(VisitItem.position, VisitItem.id)).mappings().all()
        if not rows:
            return None
        first = rows[0]
        return StoredVisitPrice(first['id'], first['currency_snapshot'], first['price'],
            first['service_id'], first['specialist_id'], tuple(StoredVisitPriceItem(
                row['item_id'], row['position'], row['item_service_id'], row['item_master_id'], row['item_price'])
                for row in rows if row['item_id'] is not None))
