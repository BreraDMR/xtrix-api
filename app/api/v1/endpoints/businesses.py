"""Business management endpoints."""
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api import deps
from app.api.core.i18n import _, localize_response
from app.api.crud import crud_business
from app.db.models.user import User
from app.schemas.business import BusinessCreate, BusinessOut, BusinessUpdate

router = APIRouter()


@router.post("/", response_model=BusinessOut, summary="Create business")
def create_business(
    business: BusinessCreate,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
    locale: str = Depends(deps.get_locale),
):
    """
    Создать новый бизнес.
    Доступно для: admin, business_owner.
    После создания бизнеса, business_id автоматически привязывается к текущему пользователю.
    """
    # Только admin и business_owner могут создавать бизнесы
    if current_user.role not in ["admin", "business_owner"]:
        raise HTTPException(status_code=403, detail=_("not_allowed", locale))
    
    # Проверяем, нет ли уже бизнеса у пользователя
    if current_user.business_id:
        raise HTTPException(
            status_code=400,
            detail=_("user_already_has_business", locale) or "User already has a business"
        )
    
    # Создаем бизнес
    new_business = crud_business.create_business(db=db, business_in=business)
    
    # Привязываем бизнес к текущему пользователю
    current_user.business_id = new_business.id
    db.add(current_user)
    db.commit()
    db.refresh(current_user)
    
    return BusinessOut.model_validate(new_business)


@router.get("/", response_model=List[BusinessOut], summary="List businesses")
def list_businesses(
    search: Optional[str] = None,  # Поиск по названию бизнеса
    skip: int = 0,
    limit: int = 100,
    db: Session = Depends(deps.get_db),
    current_user: Optional[User] = Depends(deps.get_current_user_optional),  # Опциональная авторизация для публичного поиска
    locale: str = Depends(deps.get_locale),
):
    """
    Получить список бизнесов.
    
    - Если пользователь не авторизован: возвращает все бизнесы (для поиска при регистрации)
    - Админы видят все бизнесы
    - Остальные видят только свой бизнес
    
    Параметр search позволяет искать бизнесы по названию (для публичного использования при регистрации).
    """
    from app.db.models.business import Business
    from sqlalchemy import or_
    
    query = db.query(Business).filter(Business.deleted_at.is_(None))
    
    # Если указан поиск, фильтруем по названию
    if search:
        query = query.filter(Business.name.ilike(f"%{search}%"))
    
    # Фильтрация по доступу
    if current_user is None:
        # Неавторизованный пользователь - видит все бизнесы (для поиска при регистрации)
        businesses = query.offset(skip).limit(limit).all()
    elif current_user.role == "admin":
        # Админ видит все бизнесы
        businesses = query.offset(skip).limit(limit).all()
    else:
        # Остальные видят только свой бизнес
        if current_user.business_id:
            business = crud_business.get_business(db=db, business_id=current_user.business_id)
            businesses = [business] if business else []
        else:
            businesses = []
    
    return [BusinessOut.model_validate(b) for b in businesses]


@router.get("/me", response_model=BusinessOut, summary="Get my business")
def get_my_business(
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
    locale: str = Depends(deps.get_locale),
):
    """Получить информацию о своем бизнесе."""
    if not current_user.business_id:
        raise HTTPException(
            status_code=404,
            detail=_("business_not_found", locale) or "Business not found"
        )
    
    business = crud_business.get_business(db=db, business_id=current_user.business_id)
    if not business:
        raise HTTPException(
            status_code=404,
            detail=_("business_not_found", locale) or "Business not found"
        )
    
    return BusinessOut.model_validate(business)


@router.get("/{business_id}", response_model=BusinessOut, summary="Get business by ID")
def get_business(
    business_id: int,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
    locale: str = Depends(deps.get_locale),
):
    """Получить информацию о бизнесе по ID."""
    business = crud_business.get_business(db=db, business_id=business_id)
    if not business:
        raise HTTPException(
            status_code=404,
            detail=_("business_not_found", locale) or "Business not found"
        )
    
    # Проверяем доступ: админ может видеть все, остальные - только свой бизнес
    if current_user.role != "admin" and current_user.business_id != business_id:
        raise HTTPException(status_code=403, detail=_("not_allowed", locale))
    
    return BusinessOut.model_validate(business)


@router.put("/{business_id}", response_model=BusinessOut, summary="Update business")
def update_business(
    business_id: int,
    business_update: BusinessUpdate,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
    locale: str = Depends(deps.get_locale),
):
    """Обновить информацию о бизнесе."""
    business = crud_business.get_business(db=db, business_id=business_id)
    if not business:
        raise HTTPException(
            status_code=404,
            detail=_("business_not_found", locale) or "Business not found"
        )
    
    # Проверяем доступ: админ может обновлять все, остальные - только свой бизнес
    if current_user.role != "admin" and current_user.business_id != business_id:
        raise HTTPException(status_code=403, detail=_("not_allowed", locale))
    
    updated_business = crud_business.update_business(
        db=db,
        business=business,
        business_update=business_update
    )
    
    return localize_response(
        updated_business.__dict__ if hasattr(updated_business, '__dict__') else updated_business,
        locale
    )


@router.delete("/{business_id}", response_model=BusinessOut, summary="Delete business")
def delete_business(
    business_id: int,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
    locale: str = Depends(deps.get_locale),
):
    """Удалить бизнес (soft delete). Только для админов."""
    if current_user.role != "admin":
        raise HTTPException(status_code=403, detail=_("not_allowed", locale))
    
    business = crud_business.get_business(db=db, business_id=business_id)
    if not business:
        raise HTTPException(
            status_code=404,
            detail=_("business_not_found", locale) or "Business not found"
        )
    
    deleted_business = crud_business.delete_business(db=db, business=business)
    
    return localize_response(
        deleted_business.__dict__ if hasattr(deleted_business, '__dict__') else deleted_business,
        locale
    )

