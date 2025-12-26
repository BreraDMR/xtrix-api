from datetime import datetime
from typing import Optional, Tuple

from sqlalchemy.orm import Session, joinedload

from app.api.core.pagination import PaginationParams
from app.api.core.soft_delete import filter_active, soft_delete
from app.db.models.visit import Visit
from app.schemas.visit import VisitCreate, VisitUpdate


def create_visit(db: Session, visit_in: VisitCreate, business_id: Optional[int] = None) -> Visit:
    """
    Create a new visit.
    
    Args:
        db: Database session
        visit_in: Visit data
        business_id: Business ID to associate the visit with (if None, will try to get from specialist)
    """
    visit_data = visit_in.dict()
    
    # Проверяем наличие колонки business_id в таблице visits
    from sqlalchemy import inspect
    inspector = inspect(db.bind)
    has_business_id_column = False
    try:
        columns = [col['name'] for col in inspector.get_columns('visits')]
        has_business_id_column = 'business_id' in columns
    except:
        pass
    
    # Set business_id if provided and column exists, otherwise try to get it from specialist
    if has_business_id_column:
        if business_id is None:
            # Try to get business_id from specialist
            from app.db.models.user import User
            specialist = db.query(User).filter(User.id == visit_data.get('specialist_id')).first()
            if specialist and hasattr(specialist, 'business_id') and specialist.business_id:
                business_id = specialist.business_id
        
        if business_id:
            visit_data['business_id'] = business_id
    else:
        # Если колонки нет, убираем business_id из данных
        visit_data.pop('business_id', None)
    
    visit = Visit(**visit_data)
    db.add(visit)
    db.commit()
    db.refresh(visit)
    return visit


def update_visit(db: Session, visit: Visit, update: VisitUpdate) -> Visit:
    for field, value in update.dict(exclude_unset=True).items():
        setattr(visit, field, value)
    db.add(visit)
    db.commit()
    db.refresh(visit)
    return visit


def get_visit(
    db: Session, 
    visit_id: int, 
    include_deleted: bool = False,
    business_id: Optional[int] = None  # Для проверки доступа
) -> Optional[Visit]:
    """Get visit with optimized query and optional soft-deleted inclusion."""
    query = db.query(Visit).options(
        joinedload(Visit.client),
        joinedload(Visit.specialist),
        joinedload(Visit.service),
    )
    if not include_deleted:
        query = filter_active(query)
    
    query = query.filter(Visit.id == visit_id)
    
    # Если указан business_id, фильтруем по нему для изоляции
    # Но также показываем записи с NULL business_id для обратной совместимости
    if business_id:
        # Проверяем наличие колонки business_id в таблице visits
        from sqlalchemy import inspect
        inspector = inspect(db.bind)
        has_business_id_column = False
        try:
            columns = [col['name'] for col in inspector.get_columns('visits')]
            has_business_id_column = 'business_id' in columns
        except:
            pass
        
        if has_business_id_column:
            from sqlalchemy import or_
            query = query.filter(or_(
                Visit.business_id == business_id,
                Visit.business_id.is_(None)  # Показываем старые записи без business_id
            ))
        # Если колонки нет, показываем все визиты (обратная совместимость)
    
    return query.first()


def list_visits(
    db: Session,
    client_id: Optional[int] = None,
    specialist_id: Optional[int] = None,
    business_id: Optional[int] = None,  # Новый параметр для изоляции по бизнесу
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    pagination: Optional[PaginationParams] = None,
    include_deleted: bool = False,
    include_archived: bool = False,
) -> Tuple[list[Visit], int]:
    """List visits with optional pagination. Returns (items, total_count)."""
    # Проверяем наличие колонки business_id перед выполнением запроса
    from sqlalchemy import inspect, text
    inspector = inspect(db.bind)
    has_business_id_column = False
    try:
        columns = [col['name'] for col in inspector.get_columns('visits')]
        has_business_id_column = 'business_id' in columns
    except:
        pass
    
    # Если колонки business_id нет, используем raw SQL сразу
    if not has_business_id_column:
        # Используем raw SQL для всех операций
        count_sql = "SELECT COUNT(*) FROM visits WHERE 1=1"
        params = {}
        
        if not include_deleted:
            count_sql += " AND deleted_at IS NULL"
        if not include_archived:
            count_sql += " AND archived_at IS NULL"
        if client_id:
            count_sql += " AND client_id = :client_id"
            params['client_id'] = client_id
        if specialist_id:
            count_sql += " AND specialist_id = :specialist_id"
            params['specialist_id'] = specialist_id
        if date_from:
            count_sql += " AND date_start >= :date_from"
            params['date_from'] = date_from
        if date_to:
            count_sql += " AND date_start <= :date_to"
            params['date_to'] = date_to
        
        total = db.execute(text(count_sql), params).scalar()
        
        # Получаем данные
        select_sql = """
            SELECT id, client_id, specialist_id, service_id, date_start, date_end, 
                   service_name, note, photo_url, price, status, created_at, 
                   deleted_at, archived_at
            FROM visits
            WHERE 1=1
        """
        select_params = {}
        
        if not include_deleted:
            select_sql += " AND deleted_at IS NULL"
        if not include_archived:
            select_sql += " AND archived_at IS NULL"
        if client_id:
            select_sql += " AND client_id = :client_id"
            select_params['client_id'] = client_id
        if specialist_id:
            select_sql += " AND specialist_id = :specialist_id"
            select_params['specialist_id'] = specialist_id
        if date_from:
            select_sql += " AND date_start >= :date_from"
            select_params['date_from'] = date_from
        if date_to:
            select_sql += " AND date_start <= :date_to"
            select_params['date_to'] = date_to
        
        if pagination:
            select_sql += " LIMIT :limit OFFSET :offset"
            select_params['limit'] = pagination.limit
            select_params['offset'] = pagination.offset
        
        result = db.execute(text(select_sql), select_params)
        # Конвертируем результат в объекты Visit
        items = []
        
        # Проверяем наличие business_id в таблице services для безопасной загрузки
        services_has_business_id = False
        try:
            service_columns = [col['name'] for col in inspector.get_columns('services')]
            services_has_business_id = 'business_id' in service_columns
        except:
            pass
        
        for row in result:
            visit = Visit()
            for key, value in row._mapping.items():
                setattr(visit, key, value)
            # Загружаем связанные объекты отдельно
            if visit.client_id:
                from app.db.models.client import Client
                visit.client = db.query(Client).filter(Client.id == visit.client_id).first()
            if visit.specialist_id:
                from app.db.models.user import User
                visit.specialist = db.query(User).filter(User.id == visit.specialist_id).first()
            if visit.service_id:
                from app.db.models.service import Service
                # Используем raw SQL для Service, если колонки business_id нет
                if not services_has_business_id:
                    service_row = db.execute(
                        text("SELECT id, specialist_id, name, duration_minutes, price, is_active, created_at, deleted_at, archived_at FROM services WHERE id = :id"),
                        {"id": visit.service_id}
                    ).first()
                    if service_row:
                        service = Service()
                        for key, value in service_row._mapping.items():
                            setattr(service, key, value)
                        visit.service = service
                else:
                    visit.service = db.query(Service).filter(Service.id == visit.service_id).first()
            items.append(visit)
        
        return items, total
    
    # Optimized query with joinedload for relationships (только если колонка существует)
    q = db.query(Visit).options(
        joinedload(Visit.client),
        joinedload(Visit.specialist),
        joinedload(Visit.service),
    )
    
    # Filter out deleted and archived by default
    if not include_deleted:
        q = filter_active(q)
    if not include_archived:
        q = q.filter(Visit.archived_at.is_(None))
    
    # Apply filters
    # Приоритет: если указан business_id, используем его для изоляции данных
    # Но также показываем записи с NULL business_id для обратной совместимости
    if business_id and has_business_id_column:
        from sqlalchemy import or_
        q = q.filter(or_(
            Visit.business_id == business_id,
            Visit.business_id.is_(None)  # Показываем старые записи без business_id
        ))
        # Если колонки нет, показываем все визиты (обратная совместимость)
    
    if client_id:
        q = q.filter(Visit.client_id == client_id)
    if specialist_id:
        q = q.filter(Visit.specialist_id == specialist_id)
    if date_from:
        q = q.filter(Visit.date_start >= date_from)
    if date_to:
        q = q.filter(Visit.date_start <= date_to)
    
    # Get total count before pagination
    # Используем try-except для обработки ошибок с отсутствующей колонкой
    try:
        total = q.count()
    except Exception as e:
        # Если count падает из-за отсутствующей колонки business_id, используем альтернативный способ
        if 'business_id' in str(e).lower() or 'does not exist' in str(e).lower():
            # Используем raw SQL для count без business_id
            from sqlalchemy import text
            count_sql = "SELECT COUNT(*) FROM visits WHERE 1=1"
            params = {}
            
            if not include_deleted:
                count_sql += " AND deleted_at IS NULL"
            if not include_archived:
                count_sql += " AND archived_at IS NULL"
            if client_id:
                count_sql += " AND client_id = :client_id"
                params['client_id'] = client_id
            if specialist_id:
                count_sql += " AND specialist_id = :specialist_id"
                params['specialist_id'] = specialist_id
            if date_from:
                count_sql += " AND date_start >= :date_from"
                params['date_from'] = date_from
            if date_to:
                count_sql += " AND date_start <= :date_to"
                params['date_to'] = date_to
            
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
                SELECT id, client_id, specialist_id, service_id, date_start, date_end, 
                       service_name, note, photo_url, price, status, created_at, 
                       deleted_at, archived_at
                FROM visits
                WHERE 1=1
            """
            params = {}
            
            if not include_deleted:
                select_sql += " AND deleted_at IS NULL"
            if not include_archived:
                select_sql += " AND archived_at IS NULL"
            if client_id:
                select_sql += " AND client_id = :client_id"
                params['client_id'] = client_id
            if specialist_id:
                select_sql += " AND specialist_id = :specialist_id"
                params['specialist_id'] = specialist_id
            if date_from:
                select_sql += " AND date_start >= :date_from"
                params['date_from'] = date_from
            if date_to:
                select_sql += " AND date_start <= :date_to"
                params['date_to'] = date_to
            
            if pagination:
                select_sql += " LIMIT :limit OFFSET :offset"
                params['limit'] = pagination.limit
                params['offset'] = pagination.offset
            
            result = db.execute(text(select_sql), params)
            # Конвертируем результат в объекты Visit
            items = []
            for row in result:
                visit = Visit()
                for key, value in row._mapping.items():
                    setattr(visit, key, value)
                items.append(visit)
        else:
            raise
    
    return items, total



