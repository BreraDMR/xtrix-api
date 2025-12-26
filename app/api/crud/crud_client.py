from typing import Optional, Tuple
from datetime import date

from sqlalchemy.orm import Session
from sqlalchemy import func, and_

from app.api.core.redis_client import (
    cache_client, get_cached_client, invalidate_client,
    cache_client_list, get_cached_client_list, invalidate_client_list,
)
from app.api.core.pagination import PaginationParams
from app.api.core.qr_code import generate_qr_code_data, generate_access_password
from app.api.core.security import get_password_hash, verify_password
from app.api.core.soft_delete import filter_active, soft_delete, restore_soft_deleted
from sqlalchemy.orm import joinedload
from sqlalchemy import or_
from app.db.models.client import Client
from app.db.models.client_specialist_relation import ClientSpecialistRelation
from app.schemas.client import ClientCreate, ClientUpdate, ClientOut


def find_client_by_identifiers(
    db: Session,
    contact_phone: Optional[str] = None,
    contact_email: Optional[str] = None,
    full_name: Optional[str] = None,
    birth_date: Optional[date] = None,
) -> Optional[Client]:
    """
    Ищет существующего клиента по идентификаторам.
    Приоритет поиска:
    1. По контактному телефону (если указан)
    2. По email (если указан)
    3. По ФИО + дата рождения (если указаны оба)
    
    Returns:
        Client если найден, None если не найден
    """
    query = db.query(Client)
    query = filter_active(query)  # Исключаем удаленных клиентов
    
    # Приоритет 1: Поиск по телефону
    if contact_phone:
        phone_cleaned = contact_phone.strip()
        client = query.filter(Client.contact_phone == phone_cleaned).first()
        if client:
            return client
    
    # Приоритет 2: Поиск по email
    if contact_email:
        email_cleaned = contact_email.strip().lower()
        client = query.filter(func.lower(Client.contact_email) == email_cleaned).first()
        if client:
            return client
    
    # Приоритет 3: Поиск по ФИО + дата рождения
    if full_name and birth_date:
        name_cleaned = full_name.strip()
        client = query.filter(
            and_(
                func.lower(Client.full_name) == func.lower(name_cleaned),
                Client.birth_date == birth_date
            )
        ).first()
        if client:
            return client
    
    return None


def find_or_create_client(
    db: Session,
    client_in: ClientCreate,
    business_id: Optional[int] = None,
) -> Tuple[Client, bool]:
    """
    Ищет существующего клиента или создает нового.
    
    Args:
        db: Database session
        client_in: Данные клиента для поиска/создания
        business_id: ID бизнеса (опционально, для создания связей)
    
    Returns:
        Tuple[Client, bool]: (клиент, был_ли_создан_новый)
    """
    # Поиск существующего клиента
    existing_client = find_client_by_identifiers(
        db=db,
        contact_phone=client_in.contact_phone,
        contact_email=client_in.contact_email,
        full_name=client_in.full_name,
        birth_date=client_in.birth_date,
    )
    
    if existing_client:
        # Клиент найден - возвращаем существующего
        return existing_client, False
    
    # Клиент не найден - создаем нового
    # Создаем клиента без owner_id (глобальный клиент)
    client_data = client_in.dict()
    client_data.pop('owner_id', None)  # Убираем owner_id для глобального клиента
    
    client = Client(**client_data)
    db.add(client)
    db.flush()  # Получаем ID клиента до commit
    
    # Создаем связь с бизнесом, если указан business_id
    if business_id:
        from app.db.models.client_business_relation import ClientBusinessRelation
        business_relation = ClientBusinessRelation(
            client_id=client.id,
            business_id=business_id
        )
        db.add(business_relation)
    
    db.commit()
    db.refresh(client)
    
    # Cache the new client
    try:
        cache_client(client.id, ClientOut.from_orm(client).dict())
        # Invalidate list cache
        invalidate_client_list()
    except Exception:
        pass
    
    return client, True


def create_client(db: Session, client_in: ClientCreate) -> Client:
    """
    Создает нового клиента без поиска существующих.
    Используйте find_or_create_client для автоматического поиска дубликатов.
    """
    client_data = client_in.dict()
    client_data.pop('owner_id', None)  # Убираем owner_id для глобального клиента
    
    client = Client(**client_data)
    db.add(client)
    db.commit()
    db.refresh(client)
    # Cache the new client
    try:
        cache_client(client.id, ClientOut.from_orm(client).dict())
        # Invalidate list cache
        invalidate_client_list()
    except Exception:
        pass
    return client


def update_client(db: Session, client: Client, update: ClientUpdate) -> Client:
    for field, value in update.dict(exclude_unset=True).items():
        setattr(client, field, value)
    db.add(client)
    db.commit()
    db.refresh(client)
    # Invalidate caches
    invalidate_client(client.id)
    invalidate_client_list()
    # Re-cache the updated client
    try:
        cache_client(client.id, ClientOut.from_orm(client).dict())
    except Exception:
        pass
    return client


def get_client(db: Session, client_id: int, include_deleted: bool = False) -> Optional[Client]:
    """Get client with optimized query and optional soft-deleted inclusion."""
    cached = get_cached_client(client_id)
    if cached and not include_deleted:
        return ClientOut(**cached)  # type: ignore
    
    query = db.query(Client).options(joinedload(Client.owner))
    if not include_deleted:
        query = filter_active(query)
    return query.filter(Client.id == client_id).first()


def list_clients(
    db: Session,
    owner_id: Optional[int] = None,
    business_id: Optional[int] = None,  # Новый параметр для изоляции по бизнесу
    phone: Optional[str] = None,
    name: Optional[str] = None,
    pagination: Optional[PaginationParams] = None,
    include_deleted: bool = False,
    include_archived: bool = False,
) -> Tuple[list[Client], int]:
    """List clients with optional pagination. Returns (items, total_count)."""
    # Check cache first (only for non-paginated or first page)
    cache_filters = {
        'owner_id': owner_id,
        'phone': phone,
        'name': name,
    }
    
    # Only use cache if no pagination or first page and not including deleted/archived
    use_cache = (pagination is None or pagination.page == 1) and not include_deleted and not include_archived
    
    if use_cache:
        cached = get_cached_client_list(cache_filters)
        if cached:
            # Convert to Client objects (simplified - in production might need full ORM)
            # For now, return from DB but cache the result
            pass
    
    # Optimized query with joinedload for owner
    q = db.query(Client).options(joinedload(Client.owner))
    
    # Filter out deleted and archived by default
    if not include_deleted:
        q = filter_active(q)
    if not include_archived:
        q = q.filter(Client.archived_at.is_(None))
    
    # Apply filters
    # Приоритет: если указан business_id, используем его для изоляции данных
    # Но также показываем клиентов без связей для обратной совместимости
    if business_id:
        # Клиенты, связанные с бизнесом через ClientBusinessRelation или ClientSpecialistRelation
        from app.db.models.client_business_relation import ClientBusinessRelation
        business_client_ids = db.query(ClientBusinessRelation.client_id).filter(
            ClientBusinessRelation.business_id == business_id
        )
        specialist_client_ids = db.query(ClientSpecialistRelation.client_id).filter(
            ClientSpecialistRelation.business_id == business_id
        )
        if owner_id:
            specialist_client_ids = specialist_client_ids.filter(
                ClientSpecialistRelation.specialist_id == owner_id
            )
        
        # Объединяем: клиенты с бизнес-связями + клиенты без связей (обратная совместимость)
        all_client_ids = business_client_ids.union(specialist_client_ids).subquery()
        q = q.filter(or_(
            Client.id.in_(db.query(all_client_ids)),
            ~Client.id.in_(db.query(ClientBusinessRelation.client_id).distinct())  # Клиенты без бизнес-связей
        ))
    elif owner_id:
        # Для обратной совместимости: фильтр по owner_id через ClientSpecialistRelation
        # Clients owned by the specialist OR linked through relations
        q = q.filter(
            or_(
                Client.owner_id == owner_id,
                Client.id.in_(
                    db.query(ClientSpecialistRelation.client_id).filter(
                        ClientSpecialistRelation.specialist_id == owner_id
                    )
                )
            )
        )
    if phone:
        q = q.filter(Client.contact_phone == phone)
    if name:
        q = q.filter(Client.full_name.ilike(f"%{name}%"))
    
    # Get total count before pagination
    total = q.count()
    
    # Apply pagination if provided
    if pagination:
        q = q.offset(pagination.offset).limit(pagination.limit)
    
    items = q.all()
    
    # Cache result if first page
    if use_cache and items:
        try:
            items_dict = [ClientOut.from_orm(item).dict() for item in items]
            cache_client_list(items_dict, cache_filters, ttl_seconds=300)
        except Exception:
            pass  # Fail silently
    
    return items, total


def get_client_by_qr_code(db: Session, qr_code: str) -> Optional[Client]:
    """Get client by QR code."""
    return db.query(Client).filter(Client.qr_code == qr_code).first()


def generate_client_qr_code(db: Session, client_id: int) -> tuple[str, str]:
    """
    Generate QR code and access password for a client.
    Returns (qr_code_data, access_password).
    """
    client = db.query(Client).filter(Client.id == client_id).first()
    if not client:
        raise ValueError("Client not found")
    
    # Generate QR code data
    qr_data = generate_qr_code_data(client_id)
    
    # Generate access password
    access_password = generate_access_password()
    hashed_password = get_password_hash(access_password)
    
    # Update client
    client.qr_code = qr_data
    client.access_password = hashed_password
    db.commit()
    db.refresh(client)
    
    invalidate_client(client_id)
    
    return qr_data, access_password


def verify_client_access_password(db: Session, client_id: int, password: str) -> bool:
    """Verify client access password."""
    client = get_client(db, client_id)
    if not client or not client.access_password:
        return False
    return verify_password(password, client.access_password)

