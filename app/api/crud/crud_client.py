from typing import Optional, Tuple

from sqlalchemy.orm import Session
from sqlalchemy import func

from app.api.core.redis_client import (
    cache_client, get_cached_client, invalidate_client,
    cache_client_list, get_cached_client_list, invalidate_client_list,
)
from app.api.core.pagination import PaginationParams
from app.api.core.qr_code import generate_qr_code_data, generate_access_password
from app.api.core.security import get_password_hash, verify_password
from app.api.core.soft_delete import filter_active, soft_delete, restore_soft_deleted
from sqlalchemy.orm import joinedload
from app.db.models.client import Client
from app.schemas.client import ClientCreate, ClientUpdate, ClientOut


def create_client(db: Session, client_in: ClientCreate) -> Client:
    client = Client(**client_in.dict())
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
    if owner_id:
        q = q.filter(Client.owner_id == owner_id)
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

