from typing import List, Optional
from sqlalchemy.orm import Session
from sqlalchemy import and_, or_
from app.db.models.product import Product
from app.schemas.product import ProductCreate, ProductUpdate
def create_product(db: Session, product_in: ProductCreate) -> Product:
    product = Product(**product_in.model_dump())
    db.add(product)
    db.commit()
    db.refresh(product)
    return product
def get_product(db: Session, product_id: int, business_id: Optional[int] = None) -> Optional[Product]:
    query = db.query(Product).filter(Product.id == product_id)
    if business_id:
        query = query.filter(Product.business_id == business_id)
    return query.first()
def list_products(
    db: Session,
    business_id: Optional[int] = None,
    category: Optional[str] = None,
    is_active: Optional[bool] = True,
    search: Optional[str] = None,
    low_stock_only: bool = False,
    skip: int = 0,
    limit: int = 100,
) -> List[Product]:
    query = db.query(Product)
    if business_id:
        query = query.filter(Product.business_id == business_id)
    if is_active is not None:
        query = query.filter(Product.is_active == is_active)
    if category:
        query = query.filter(Product.category == category)
    if search:
        search_term = f"%{search}%"
        query = query.filter(
            or_(
                Product.name.ilike(search_term),
                Product.description.ilike(search_term),
                Product.barcode.ilike(search_term),
            )
        )
    if low_stock_only:
        query = query.filter(Product.quantity <= Product.min_quantity)
    return query.order_by(Product.name).offset(skip).limit(limit).all()
def update_product(db: Session, product: Product, product_in: ProductUpdate) -> Product:
    update_data = product_in.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(product, field, value)
    db.commit()
    db.refresh(product)
    return product
def delete_product(db: Session, product_id: int, business_id: Optional[int] = None) -> bool:
    product = get_product(db, product_id, business_id)
    if not product:
        return False
    product.is_active = False
    db.commit()
    return True
def add_product_quantity(db: Session, product_id: int, quantity: float, business_id: Optional[int] = None) -> Optional[Product]:
    product = get_product(db, product_id, business_id)
    if not product:
        return None
    product.quantity += quantity
    db.commit()
    db.refresh(product)
    return product
def reduce_product_quantity(db: Session, product_id: int, quantity: float, business_id: Optional[int] = None) -> Optional[Product]:
    product = get_product(db, product_id, business_id)
    if not product:
        return None
    if product.quantity < quantity:
        raise ValueError(f"Недостаточно товара на складе. Доступно: {product.quantity}, требуется: {quantity}")
    product.quantity -= quantity
    db.commit()
    db.refresh(product)
    return product
def get_low_stock_products(db: Session, business_id: Optional[int] = None) -> List[Product]:
    query = db.query(Product).filter(
        and_(
            Product.quantity <= Product.min_quantity,
            Product.is_active == True,
        )
    )
    if business_id:
        query = query.filter(Product.business_id == business_id)
    return query.order_by(Product.quantity.asc()).all()
