from typing import Set, Optional, Tuple
from datetime import date, timedelta
from sqlalchemy.orm import Session
from app.api.core import audit_service
from app.api.core.clock import utcnow
from sqlalchemy import func, and_
from app.api.core.redis_client import (
    cache_client, get_cached_client, invalidate_client,
    cache_client_list, get_cached_client_list, invalidate_client_list,
)
from app.api.core.pagination import PaginationParams
from app.api.core.qr_code import generate_qr_code_data, generate_access_password
from app.api.core.security import get_password_hash, verify_password
from app.api.core.soft_delete import filter_active
from sqlalchemy.orm import joinedload
from sqlalchemy import or_
from app.db.models.client import Client
from app.db.models.client_specialist_relation import ClientSpecialistRelation
from app.schemas.client import ClientCreate, ClientUpdate, ClientOut
def find_duplicate_client(
    db: Session,
    contact_phone: Optional[str] = None,
    contact_email: Optional[str] = None,
    full_name: Optional[str] = None,
    birth_date: Optional[date] = None,
    within_ids: Optional[Set[int]] = None,
) -> Optional[Tuple[Client, str]]:
    """The existing client these identifiers point at, plus which field matched."""
    query = db.query(Client)
    query = filter_active(query)  
    if within_ids is not None:
        # only ever match among clients the caller already has access to
        if not within_ids:
            return None
        query = query.filter(Client.id.in_(within_ids))
    if contact_phone:
        phone_cleaned = contact_phone.strip()
        client = query.filter(Client.contact_phone == phone_cleaned).first()
        if client:
            return client, "contact_phone"
    if contact_email:
        email_cleaned = contact_email.strip().lower()
        client = query.filter(func.lower(Client.contact_email) == email_cleaned).first()
        if client:
            return client, "contact_email"
    if full_name and birth_date:
        name_cleaned = full_name.strip()
        client = query.filter(
            and_(
                func.lower(Client.full_name) == func.lower(name_cleaned),
                Client.birth_date == birth_date
            )
        ).first()
        if client:
            return client, "full_name+birth_date"
    return None
def find_client_by_identifiers(
    db: Session,
    contact_phone: Optional[str] = None,
    contact_email: Optional[str] = None,
    full_name: Optional[str] = None,
    birth_date: Optional[date] = None,
    within_ids: Optional[Set[int]] = None,
) -> Optional[Client]:
    found = find_duplicate_client(db, contact_phone, contact_email, full_name, birth_date, within_ids)
    return found[0] if found else None
def validate_healthcare_required_fields(
    db: Session,
    client_id: int,
    specialist_id: Optional[int] = None,
) -> None:
    if not specialist_id:
        return  
    from app.db.models.user import User
    specialist = db.query(User).filter(User.id == specialist_id).first()
    if not specialist or not specialist.specialist_type:
        return  
    try:
        from app.db.models.specialist_type import SpecialistType, get_category_for_type, SpecialistCategory, REQUIRED_FIELDS_BY_CATEGORY
        specialist_type_enum = SpecialistType(specialist.specialist_type)
        category = get_category_for_type(specialist_type_enum)
        if category != SpecialistCategory.HEALTHCARE:
            return
        required_fields = REQUIRED_FIELDS_BY_CATEGORY.get(SpecialistCategory.HEALTHCARE, [])
        if not required_fields:
            return
        from app.db.models.medical_profile import MedicalProfile
        medical_profile = db.query(MedicalProfile).filter(
            MedicalProfile.client_id == client_id,
            MedicalProfile.deleted_at.is_(None)
        ).first()
        if not medical_profile:
            raise ValueError(
                f"For healthcare specialists, medical profile with required fields ({', '.join(required_fields)}) must be created. "
                "Please create MedicalProfile with allergies and chronic_diseases fields."
            )
        missing_fields = []
        for field in required_fields:
            value = getattr(medical_profile, field, None)
            if value is None or (isinstance(value, str) and not value.strip()):
                missing_fields.append(field)
        if missing_fields:
            raise ValueError(
                f"For healthcare specialists, the following medical fields are required but missing: {', '.join(missing_fields)}. "
                "Please fill in these fields in MedicalProfile."
            )
    except (ValueError, AttributeError) as e:
        if "healthcare" in str(e).lower() or "required" in str(e).lower():
            raise
        pass
def create_client_for(
    db: Session,
    client_in: ClientCreate,
    business_id: Optional[int] = None,
    specialist_id: Optional[int] = None,
) -> Client:
    client_data = client_in.model_dump()
    client_data.pop('owner_id', None)  
    client = Client(**client_data)
    if specialist_id and not business_id:
        # solo specialist: no business to hang relations on, so ownership is the only link
        client.owner_id = specialist_id
    db.add(client)
    db.flush()  
    if specialist_id:
        try:
            validate_healthcare_required_fields(db, client.id, specialist_id)
        except ValueError as e:
            db.rollback()
            raise
    if business_id:
        from sqlalchemy import inspect
        inspector = inspect(db.bind)
        has_business_relations = 'client_business_relations' in inspector.get_table_names()
        if has_business_relations:
            from app.db.models.client_business_relation import ClientBusinessRelation
            business_relation = ClientBusinessRelation(
                client_id=client.id,
                business_id=business_id
            )
            db.add(business_relation)
    db.commit()
    db.refresh(client)
    try:
        cache_client(client.id, ClientOut.model_validate(client).model_dump())
        invalidate_client_list()
    except Exception:
        pass
    return client
def create_client(db: Session, client_in: ClientCreate, specialist_id: Optional[int] = None) -> Client:
    client_data = client_in.model_dump()
    client_data.pop('owner_id', None)  
    client = Client(**client_data)
    db.add(client)
    db.flush()  
    if specialist_id:
        try:
            validate_healthcare_required_fields(db, client.id, specialist_id)
        except ValueError as e:
            db.rollback()
            raise
    db.commit()
    db.refresh(client)
    try:
        cache_client(client.id, ClientOut.model_validate(client).model_dump())
        invalidate_client_list()
    except Exception:
        pass
    return client
def update_client(
    db: Session,
    client: Client,
    update: ClientUpdate,
    *,
    business_id: Optional[int] = None,
    owner_user_id: Optional[int] = None,
    actor=None,
    request_id: Optional[str] = None,
) -> Client:
    data = update.model_dump(exclude_unset=True)
    changed = [field for field, value in data.items() if getattr(client, field) != value]
    for field, value in data.items():
        setattr(client, field, value)
    if changed:
        audit_service.record_event(
            db,
            action="client.updated",
            entity_type="client",
            entity_id=client.id,
            business_id=business_id,
            owner_user_id=owner_user_id if business_id is None else None,
            changes={"fields": changed},
            actor=actor,
            request_id=request_id,
        )
    db.add(client)
    db.commit()
    db.refresh(client)
    invalidate_client(client.id)
    invalidate_client_list()
    try:
        cache_client(client.id, ClientOut.model_validate(client).model_dump())
    except Exception:
        pass
    return client
def get_client(db: Session, client_id: int, include_deleted: bool = False) -> Optional[Client]:
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
    business_id: Optional[int] = None,  
    phone: Optional[str] = None,
    name: Optional[str] = None,
    pagination: Optional[PaginationParams] = None,
    include_deleted: bool = False,
    include_archived: bool = False,
    accessible_client_ids: Optional[set[int]] = None,  
) -> Tuple[list[Client], int]:
    cache_filters = {
        'owner_id': owner_id,
        'phone': phone,
        'name': name,
    }
    use_cache = (pagination is None or pagination.page == 1) and not include_deleted and not include_archived
    if use_cache:
        cached = get_cached_client_list(cache_filters)
        if cached:
            pass
    q = db.query(Client).options(joinedload(Client.owner))
    if not include_deleted:
        q = filter_active(q)
    if not include_archived:
        q = q.filter(Client.archived_at.is_(None))
    if business_id:
        from sqlalchemy import inspect
        inspector = inspect(db.bind)
        table_names = inspector.get_table_names()
        has_business_relations = 'client_business_relations' in table_names
        has_specialist_relations = 'client_specialist_relations' in table_names
        has_specialist_business_id = False
        if has_specialist_relations:
            try:
                columns = [col['name'] for col in inspector.get_columns('client_specialist_relations')]
                has_specialist_business_id = 'business_id' in columns
            except:
                pass
        filters = []
        if owner_id:
            filters.append(Client.owner_id == owner_id)
        if has_business_relations or (has_specialist_relations and has_specialist_business_id):
            try:
                if has_business_relations:
                    from app.db.models.client_business_relation import ClientBusinessRelation
                    business_client_ids = db.query(ClientBusinessRelation.client_id).filter(
                        ClientBusinessRelation.business_id == business_id
                    )
                    filters.append(Client.id.in_(business_client_ids))
                if has_specialist_relations and has_specialist_business_id:
                    specialist_filter = ClientSpecialistRelation.business_id == business_id
                    if owner_id:
                        specialist_filter = and_(
                            specialist_filter,
                            ClientSpecialistRelation.specialist_id == owner_id
                        )
                    specialist_client_ids = db.query(ClientSpecialistRelation.client_id).filter(
                        specialist_filter
                    )
                    filters.append(Client.id.in_(specialist_client_ids))
            except Exception as e:
                pass
        if filters:
            q = q.filter(or_(*filters))
        elif owner_id:
            q = q.filter(Client.owner_id == owner_id)
    elif owner_id:
        from sqlalchemy import inspect
        inspector = inspect(db.bind)
        table_names = inspector.get_table_names()
        has_specialist_relations = 'client_specialist_relations' in table_names
        has_specialist_business_id = False
        if has_specialist_relations:
            try:
                columns = [col['name'] for col in inspector.get_columns('client_specialist_relations')]
                has_specialist_business_id = 'business_id' in columns
            except:
                pass
        if has_specialist_relations:
            try:
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
            except Exception:
                q = q.filter(Client.owner_id == owner_id)
        else:
            q = q.filter(Client.owner_id == owner_id)
    if phone:
        q = q.filter(Client.contact_phone == phone)
    if name:
        q = q.filter(Client.full_name.ilike(f"%{name}%"))
    if accessible_client_ids is not None:
        if len(accessible_client_ids) == 0:
            return [], 0
        q = q.filter(Client.id.in_(accessible_client_ids))
    total = q.count()
    if pagination:
        q = q.offset(pagination.offset).limit(pagination.limit)
    items = q.all()
    if use_cache and items:
        try:
            items_dict = [ClientOut.model_validate(item).model_dump() for item in items]
            cache_client_list(items_dict, cache_filters, ttl_seconds=300)
        except Exception:
            pass  
    return items, total
def get_client_by_qr_code(db: Session, qr_code: str) -> Optional[Client]:
    return db.query(Client).filter(Client.qr_code == qr_code).first()
def generate_client_qr_code(db: Session, client_id: int) -> tuple[str, str]:
    client = db.query(Client).filter(Client.id == client_id).first()
    if not client:
        raise ValueError("Client not found")
    qr_data = generate_qr_code_data(client_id)
    access_password = generate_access_password()
    hashed_password = get_password_hash(access_password)
    client.qr_code = qr_data
    client.access_password = hashed_password
    # a fresh QR starts with a clean slate
    client.qr_failed_attempts = 0
    client.qr_locked_until = None
    db.commit()
    db.refresh(client)
    invalidate_client(client_id)
    return qr_data, access_password
QR_MAX_ATTEMPTS = 5
QR_LOCK_MINUTES = 30


def qr_locked(client: Client) -> bool:
    return bool(client.qr_locked_until and client.qr_locked_until > utcnow())


def verify_client_access_password(db: Session, client_id: int, password: str) -> bool:
    # the PIN is 4 digits, so wrong guesses count per client (from any IP) and lock the QR for a while
    client = db.query(Client).filter(Client.id == client_id).with_for_update().first()
    if not client or not client.access_password or qr_locked(client):
        return False
    if verify_password(password, client.access_password):
        client.qr_failed_attempts = 0
        db.commit()
        return True
    client.qr_failed_attempts = (client.qr_failed_attempts or 0) + 1
    if client.qr_failed_attempts >= QR_MAX_ATTEMPTS:
        client.qr_locked_until = utcnow() + timedelta(minutes=QR_LOCK_MINUTES)
        client.qr_failed_attempts = 0
    db.commit()
    return False
