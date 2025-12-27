from typing import Optional, Tuple

from sqlalchemy.orm import Session, joinedload

from app.api.core.pagination import PaginationParams
from app.api.core.soft_delete import filter_active, soft_delete
from app.db.models.service import Service
from app.schemas.service import ServiceCreate, ServiceUpdate


def create_service(db: Session, service_in: ServiceCreate, business_id: Optional[int] = None) -> Service:
    """
    Create a service. Если business_id не указан, берется из specialist_id → User → business_id.
    """
    service_data = service_in.dict()
    
    # Проверяем наличие колонки business_id в таблице services
    from sqlalchemy import inspect
    inspector = inspect(db.bind)
    has_business_id_column = False
    try:
        columns = [col['name'] for col in inspector.get_columns('services')]
        has_business_id_column = 'business_id' in columns
    except:
        pass
    
    if has_business_id_column:
        # Если business_id не указан, пытаемся получить его из specialist
        if business_id is None and service_data.get('specialist_id'):
            from app.db.models.user import User
            specialist = db.query(User).filter(User.id == service_data['specialist_id']).first()
            if specialist and hasattr(specialist, 'business_id') and specialist.business_id:
                business_id = specialist.business_id
        
        # business_id может быть None для обратной совместимости (старые услуги без business_id)
        service_data['business_id'] = business_id
    else:
        # Если колонки нет, убираем business_id из данных
        service_data.pop('business_id', None)
    
    service = Service(**service_data)
    db.add(service)
    db.commit()
    db.refresh(service)
    return service


def update_service(db: Session, service: Service, update: ServiceUpdate) -> Service:
    for field, value in update.dict(exclude_unset=True).items():
        setattr(service, field, value)
    db.add(service)
    db.commit()
    db.refresh(service)
    return service


def get_service(db: Session, service_id: int, include_deleted: bool = False) -> Optional[Service]:
    """Get service with optimized query and optional soft-deleted inclusion."""
    query = db.query(Service).options(joinedload(Service.specialist))
    if not include_deleted:
        query = filter_active(query)
    return query.filter(Service.id == service_id).first()


def list_services(
    db: Session,
    specialist_id: Optional[int] = None,
    business_id: Optional[int] = None,  # Новый параметр для изоляции по бизнесу
    is_active: Optional[bool] = None,  # Переименовано для консистентности
    pagination: Optional[PaginationParams] = None,
    include_deleted: bool = False,
    include_archived: bool = False,
) -> Tuple[list[Service], int]:
    """List services with optional pagination. Returns (items, total_count)."""
    # Проверяем наличие колонки business_id перед выполнением запроса
    from sqlalchemy import inspect
    inspector = inspect(db.bind)
    has_business_id_column = False
    try:
        columns = [col['name'] for col in inspector.get_columns('services')]
        has_business_id_column = 'business_id' in columns
    except:
        pass
    
    # Если колонки business_id нет, используем raw SQL сразу
    if not has_business_id_column:
        # Используем raw SQL для всех операций
        from sqlalchemy import text
        count_sql = "SELECT COUNT(*) FROM services WHERE 1=1"
        params = {}
        
        if not include_deleted:
            count_sql += " AND deleted_at IS NULL"
        if not include_archived:
            count_sql += " AND archived_at IS NULL"
        if specialist_id:
            count_sql += " AND specialist_id = :specialist_id"
            params['specialist_id'] = specialist_id
        if is_active is not None:
            count_sql += " AND is_active = :is_active"
            params['is_active'] = is_active
        
        total = db.execute(text(count_sql), params).scalar()
        
        # Получаем данные
        select_sql = """
            SELECT id, specialist_id, name, duration_minutes, price, is_active, 
                   created_at, deleted_at, archived_at
            FROM services
            WHERE 1=1
        """
        select_params = {}
        
        if not include_deleted:
            select_sql += " AND deleted_at IS NULL"
        if not include_archived:
            select_sql += " AND archived_at IS NULL"
        if specialist_id:
            select_sql += " AND specialist_id = :specialist_id"
            select_params['specialist_id'] = specialist_id
        if is_active is not None:
            select_sql += " AND is_active = :is_active"
            select_params['is_active'] = is_active
        
        if pagination:
            select_sql += " LIMIT :limit OFFSET :offset"
            select_params['limit'] = pagination.limit
            select_params['offset'] = pagination.offset
        
        result = db.execute(text(select_sql), select_params)
        # Конвертируем результат в объекты Service
        items = []
        for row in result:
            service = Service()
            for key, value in row._mapping.items():
                setattr(service, key, value)
            # Загружаем связанные объекты отдельно
            if service.specialist_id:
                from app.db.models.user import User
                service.specialist = db.query(User).filter(User.id == service.specialist_id).first()
            items.append(service)
        
        return items, total
    
    # Optimized query with joinedload for relationships (только если колонка существует)
    q = db.query(Service).options(joinedload(Service.specialist))
    
    # Filter out deleted and archived by default
    if not include_deleted:
        q = filter_active(q)
    if not include_archived:
        q = q.filter(Service.archived_at.is_(None))
    
    # Apply filters
    # Фильтрация по business_id:
    # - Если business_id указан (не None), показываем услуги с этим business_id И услуги с NULL business_id (старые)
    # - Если business_id не указан (None), показываем только услуги с NULL business_id (для обратной совместимости)
    if has_business_id_column:
        from sqlalchemy import or_
        if business_id is not None:
            # Показываем услуги с указанным business_id И старые услуги с NULL business_id
            q = q.filter(or_(
                Service.business_id == business_id,
                Service.business_id.is_(None)  # Показываем старые записи без business_id
            ))
        else:
            # Если business_id не указан, показываем только старые услуги с NULL business_id
            q = q.filter(Service.business_id.is_(None))
    
    if specialist_id:
        q = q.filter(Service.specialist_id == specialist_id)
    if is_active is not None:
        q = q.filter(Service.is_active == is_active)
    
    # Get total count before pagination
    # Используем try-except для обработки ошибок с отсутствующей колонкой
    try:
        total = q.count()
    except Exception as e:
        # Если count падает из-за отсутствующей колонки business_id, используем альтернативный способ
        if 'business_id' in str(e).lower() or 'does not exist' in str(e).lower():
            # Используем raw SQL для count без business_id
            from sqlalchemy import text
            count_sql = "SELECT COUNT(*) FROM services WHERE 1=1"
            params = {}
            
            if not include_deleted:
                count_sql += " AND deleted_at IS NULL"
            if not include_archived:
                count_sql += " AND archived_at IS NULL"
            if specialist_id:
                count_sql += " AND specialist_id = :specialist_id"
                params['specialist_id'] = specialist_id
            if is_active is not None:
                count_sql += " AND is_active = :is_active"
                params['is_active'] = is_active
            
            total = db.execute(text(count_sql), params).scalar()
        else:
            raise
    
    # Apply pagination if provided
    if pagination:
        q = q.offset(pagination.offset).limit(pagination.limit)
    
    # Получаем элементы с обработкой ошибок
    try:
        items = q.all()
    except Exception as e:
        # Если запрос падает из-за отсутствующей колонки business_id, используем raw SQL
        if 'business_id' in str(e).lower() or 'does not exist' in str(e).lower():
            from sqlalchemy import text
            select_sql = """
                SELECT id, specialist_id, name, duration_minutes, price, is_active, 
                       created_at, deleted_at, archived_at
                FROM services
                WHERE 1=1
            """
            params = {}
            
            if not include_deleted:
                select_sql += " AND deleted_at IS NULL"
            if not include_archived:
                select_sql += " AND archived_at IS NULL"
            if specialist_id:
                select_sql += " AND specialist_id = :specialist_id"
                params['specialist_id'] = specialist_id
            if is_active is not None:
                select_sql += " AND is_active = :is_active"
                params['is_active'] = is_active
            
            if pagination:
                select_sql += " LIMIT :limit OFFSET :offset"
                params['limit'] = pagination.limit
                params['offset'] = pagination.offset
            
            result = db.execute(text(select_sql), params)
            # Конвертируем результат в объекты Service
            items = []
            for row in result:
                service = Service()
                for key, value in row._mapping.items():
                    setattr(service, key, value)
                items.append(service)
        else:
            raise
    
    return items, total



