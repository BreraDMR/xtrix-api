from typing import List, Optional
from sqlalchemy.orm import Query, Session
from sqlalchemy import and_, or_
from app.db.models.product import Product
from app.schemas.product import ProductCreate, ProductUpdate
class BarcodeTaken(ValueError):
    """Another product of the same business already has this barcode."""


def _commit_product(db: Session, product: Product) -> Product:
    from sqlalchemy.exc import IntegrityError
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        constraint = getattr(getattr(exc.orig, "diag", None), "constraint_name", None)
        if constraint == "uq_products_business_barcode":
            raise BarcodeTaken("product_barcode_taken") from None
        raise
    db.refresh(product)
    return product


def create_product(db: Session, product_in: ProductCreate) -> Product:
    product = Product(**product_in.model_dump())
    db.add(product)
    return _commit_product(db, product)
def get_product(db: Session, product_id: int, business_id: Optional[int] = None) -> Optional[Product]:
    query = db.query(Product).filter(Product.id == product_id)
    if business_id:
        query = query.filter(Product.business_id == business_id)
    return query.first()
def query_products(
    db: Session,
    business_id: Optional[int] = None,
    category: Optional[str] = None,
    is_active: Optional[bool] = True,
    search: Optional[str] = None,
    low_stock_only: bool = False,
) -> Query[Product]:
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
    return query.order_by(Product.name, Product.id)
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
    return query_products(
        db, business_id=business_id, category=category, is_active=is_active,
        search=search, low_stock_only=low_stock_only,
    ).offset(skip).limit(limit).all()
def update_product(db: Session, product: Product, product_in: ProductUpdate) -> Product:
    update_data = product_in.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(product, field, value)
    return _commit_product(db, product)
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
        raise ValueError(f"Not enough stock. Available: {product.quantity}, requested: {quantity}")
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
