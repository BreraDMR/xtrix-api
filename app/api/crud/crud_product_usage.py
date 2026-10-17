from typing import List, Optional
from datetime import datetime, timedelta
from sqlalchemy.orm import Session
from sqlalchemy import and_
from app.db.models.product_usage import ProductUsage
from app.schemas.product_usage import ProductUsageCreate
from app.db.models.product import Product
from app.api.crud.crud_product import reduce_product_quantity
def create_product_usage(
    db: Session,
    usage_in: ProductUsageCreate,
    auto_reduce_stock: bool = True,
) -> ProductUsage:
    product = db.query(Product).filter(Product.id == usage_in.product_id).first()
    if not product:
        raise ValueError(f"Product {usage_in.product_id} not found")
    if product.business_id != usage_in.business_id:
        raise ValueError("Product doesn't belong to this business")
    if auto_reduce_stock:
        reduce_product_quantity(db, usage_in.product_id, usage_in.quantity, usage_in.business_id)
    usage = ProductUsage(**usage_in.model_dump())
    db.add(usage)
    db.commit()
    db.refresh(usage)
    return usage
def get_product_usage(db: Session, usage_id: int) -> Optional[ProductUsage]:
    return db.query(ProductUsage).filter(ProductUsage.id == usage_id).first()
def list_product_usages(
    db: Session,
    business_id: Optional[int] = None,
    product_id: Optional[int] = None,
    visit_id: Optional[int] = None,
    client_id: Optional[int] = None,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    skip: int = 0,
    limit: int = 100,
) -> List[ProductUsage]:
    query = db.query(ProductUsage)
    if business_id:
        query = query.filter(ProductUsage.business_id == business_id)
    if product_id:
        query = query.filter(ProductUsage.product_id == product_id)
    if visit_id:
        query = query.filter(ProductUsage.visit_id == visit_id)
    if client_id:
        query = query.filter(ProductUsage.client_id == client_id)
    if date_from:
        query = query.filter(ProductUsage.usage_date >= date_from)
    if date_to:
        query = query.filter(ProductUsage.usage_date <= date_to)
    return query.order_by(ProductUsage.usage_date.desc()).offset(skip).limit(limit).all()
def get_usage_stats(
    db: Session,
    product_id: int,
    days: int = 30,
    business_id: Optional[int] = None,
) -> dict:
    date_from = datetime.utcnow() - timedelta(days=days)
    query = db.query(ProductUsage).filter(
        and_(
            ProductUsage.product_id == product_id,
            ProductUsage.usage_date >= date_from,
        )
    )
    if business_id:
        query = query.filter(ProductUsage.business_id == business_id)
    usages = query.all()
    total_quantity = sum(u.quantity for u in usages)
    usage_count = len(usages)
    avg_per_usage = total_quantity / usage_count if usage_count > 0 else 0
    return {
        "product_id": product_id,
        "period_days": days,
        "total_quantity_used": total_quantity,
        "usage_count": usage_count,
        "avg_per_usage": avg_per_usage,
    }
