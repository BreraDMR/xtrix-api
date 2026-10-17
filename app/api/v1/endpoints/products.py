from typing import Optional, List
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from app.api import deps
from app.api.core.i18n import _
from app.api.core.pagination import PaginatedResponse
from app.db.models.user import User
from app.db.models.product import Product
from app.schemas.product import ProductCreate, ProductUpdate, ProductOut, ProductStockOut
from app.schemas.product_usage import ProductUsageCreate, ProductUsageOut
from app.api.crud import crud_product, crud_product_usage
from app.api.core.logger import logger
router = APIRouter()
def get_user_business_id(current_user: User, db: Session) -> Optional[int]:
    if current_user.business_id:
        return current_user.business_id
    if current_user.role == "employee":
        from app.api.crud import crud_employee
        employee = crud_employee.get_employee_by_user_id(db, current_user.id)
        if employee and employee.business_id:
            return employee.business_id
    return None
@router.post("/", response_model=ProductOut)
def create_product(
    product: ProductCreate,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.role_required("admin", "business_owner")),
    locale: str = Depends(deps.get_locale),
):
    business_id = get_user_business_id(current_user, db)
    if not business_id:
        raise HTTPException(status_code=403, detail=_("business_required", locale))
    if product.business_id != business_id:
        raise HTTPException(status_code=403, detail=_("not_allowed", locale))
    try:
        result = crud_product.create_product(db, product)
        logger.info(f"Created product {result.id} by user {current_user.id}")
        return result
    except ValueError as e:
        logger.error(f"Error creating product: {e}")
        raise HTTPException(status_code=400, detail=str(e))
@router.get("/", response_model=PaginatedResponse[ProductOut])
def list_products(
    category: Optional[str] = Query(None, description="Filter by category"),
    is_active: Optional[bool] = Query(True, description="Filter by active flag"),
    search: Optional[str] = Query(None, description="Search by name, description, barcode"),
    low_stock_only: bool = Query(False, description="Only low-stock products"),
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.role_required("admin", "business_owner", "specialist", "employee")),
    locale: str = Depends(deps.get_locale),
):
    business_id = get_user_business_id(current_user, db)
    if not business_id:
        raise HTTPException(status_code=403, detail=_("business_required", locale))
    skip = (page - 1) * per_page
    products = crud_product.list_products(
        db,
        business_id=business_id,
        category=category,
        is_active=is_active,
        search=search,
        low_stock_only=low_stock_only,
        skip=skip,
        limit=per_page,
    )
    total = db.query(Product).filter(Product.business_id == business_id).count()
    return PaginatedResponse.create(
        items=[p for p in products],
        total=total,
        page=page,
        per_page=per_page,
    )
@router.get("/low-stock", response_model=List[ProductStockOut])
def get_low_stock_products(
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.role_required("admin", "business_owner", "specialist", "employee")),
    locale: str = Depends(deps.get_locale),
):
    business_id = get_user_business_id(current_user, db)
    if not business_id:
        raise HTTPException(status_code=403, detail=_("business_required", locale))
    products = crud_product.get_low_stock_products(db, business_id=business_id)
    result = []
    for p in products:
        result.append(ProductStockOut(
            id=p.id,
            name=p.name,
            quantity=p.quantity,
            min_quantity=p.min_quantity,
            unit=p.unit,
            is_low_stock=True,
        ))
    return [r for r in result]
@router.post("/usage", response_model=ProductUsageOut)
def create_product_usage(
    usage: ProductUsageCreate,
    auto_reduce_stock: bool = Query(True, description="Deduct from stock automatically"),
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.role_required("admin", "business_owner", "specialist", "employee")),
    locale: str = Depends(deps.get_locale),
):
    business_id = get_user_business_id(current_user, db)
    if not business_id:
        raise HTTPException(status_code=403, detail=_("business_required", locale))
    if usage.business_id != business_id:
        raise HTTPException(status_code=403, detail=_("not_allowed", locale))
    if not usage.used_by_id:
        usage.used_by_id = current_user.id
    try:
        result = crud_product_usage.create_product_usage(db, usage, auto_reduce_stock=auto_reduce_stock)
        logger.info(f"Created product usage {result.id} by user {current_user.id}")
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
@router.get("/usage", response_model=PaginatedResponse[ProductUsageOut])
def list_product_usages(
    product_id: Optional[int] = Query(None),
    visit_id: Optional[int] = Query(None),
    client_id: Optional[int] = Query(None),
    date_from: Optional[datetime] = Query(None),
    date_to: Optional[datetime] = Query(None),
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.role_required("admin", "business_owner", "specialist", "employee")),
    locale: str = Depends(deps.get_locale),
):
    business_id = get_user_business_id(current_user, db)
    if not business_id:
        raise HTTPException(status_code=403, detail=_("business_required", locale))
    skip = (page - 1) * per_page
    usages = crud_product_usage.list_product_usages(
        db,
        business_id=business_id,
        product_id=product_id,
        visit_id=visit_id,
        client_id=client_id,
        date_from=date_from,
        date_to=date_to,
        skip=skip,
        limit=per_page,
    )
    from app.db.models.product_usage import ProductUsage
    total = db.query(ProductUsage).filter(ProductUsage.business_id == business_id).count()
    return PaginatedResponse.create(
        items=[u for u in usages],
        total=total,
        page=page,
        per_page=per_page,
    )

@router.get("/{product_id}", response_model=ProductOut)
def get_product(
    product_id: int,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.role_required("admin", "business_owner", "specialist", "employee")),
    locale: str = Depends(deps.get_locale),
):
    business_id = get_user_business_id(current_user, db)
    if not business_id:
        raise HTTPException(status_code=403, detail=_("business_required", locale))
    product = crud_product.get_product(db, product_id, business_id)
    if not product:
        raise HTTPException(status_code=404, detail=_("product_not_found", locale))
    return product
@router.put("/{product_id}", response_model=ProductOut)
def update_product(
    product_id: int,
    product: ProductUpdate,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.role_required("admin", "business_owner")),
    locale: str = Depends(deps.get_locale),
):
    business_id = get_user_business_id(current_user, db)
    if not business_id:
        raise HTTPException(status_code=403, detail=_("business_required", locale))
    db_product = crud_product.get_product(db, product_id, business_id)
    if not db_product:
        raise HTTPException(status_code=404, detail=_("product_not_found", locale))
    try:
        result = crud_product.update_product(db, db_product, product)
        logger.info(f"Updated product {product_id} by user {current_user.id}")
        return result
    except ValueError as e:
        logger.error(f"Error updating product: {e}")
        raise HTTPException(status_code=400, detail=str(e))
@router.delete("/{product_id}")
def delete_product(
    product_id: int,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.role_required("admin", "business_owner")),
    locale: str = Depends(deps.get_locale),
):
    business_id = get_user_business_id(current_user, db)
    if not business_id:
        raise HTTPException(status_code=403, detail=_("business_required", locale))
    success = crud_product.delete_product(db, product_id, business_id)
    if not success:
        raise HTTPException(status_code=404, detail=_("product_not_found", locale))
    logger.info(f"Deleted product {product_id} by user {current_user.id}")
    return {"message": _("product_deleted", locale)}
@router.post("/{product_id}/add-quantity")
def add_product_quantity(
    product_id: int,
    quantity: float = Query(..., gt=0, description="Quantity to add"),
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.role_required("admin", "business_owner")),
    locale: str = Depends(deps.get_locale),
):
    business_id = get_user_business_id(current_user, db)
    if not business_id:
        raise HTTPException(status_code=403, detail=_("business_required", locale))
    product = crud_product.add_product_quantity(db, product_id, quantity, business_id)
    if not product:
        raise HTTPException(status_code=404, detail=_("product_not_found", locale))
    logger.info(f"Added {quantity} to product {product_id} by user {current_user.id}")
    return product
@router.post("/{product_id}/reduce-quantity")
def reduce_product_quantity_endpoint(
    product_id: int,
    quantity: float = Query(..., gt=0, description="Quantity to deduct"),
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.role_required("admin", "business_owner", "specialist", "employee")),
    locale: str = Depends(deps.get_locale),
):
    business_id = get_user_business_id(current_user, db)
    if not business_id:
        raise HTTPException(status_code=403, detail=_("business_required", locale))
    try:
        product = crud_product.reduce_product_quantity(db, product_id, quantity, business_id)
        if not product:
            raise HTTPException(status_code=404, detail=_("product_not_found", locale))
        logger.info(f"Reduced {quantity} from product {product_id} by user {current_user.id}")
        return product
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))