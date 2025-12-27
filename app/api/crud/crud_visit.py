from datetime import datetime
from typing import Optional, Tuple

from sqlalchemy.orm import Session, joinedload

from app.api.core.pagination import PaginationParams
from app.api.core.soft_delete import filter_active, soft_delete
from app.db.models.visit import Visit
from app.schemas.visit import VisitCreate, VisitUpdate


def create_visit(db: Session, visit_in: VisitCreate, business_id: Optional[int] = None) -> Visit:
    """
    Create a new visit. Автоматически применяет цену и длительность из услуги, если service_id указан.
    
    Args:
        db: Database session
        visit_in: Visit data
        business_id: Business ID to associate the visit with (if None, will try to get from specialist)
    """
    from app.api.core.logger import get_logger
    logger = get_logger("visits")
    
    visit_data = visit_in.dict()
    logger.info(f"create_visit: visit_data keys={list(visit_data.keys())}, service_id={visit_data.get('service_id')}, price={visit_data.get('price')}, date_end={visit_data.get('date_end')}")
    
    # Автоматическое применение цены и длительности из услуги, если service_id указан
    if visit_data.get('service_id'):
        from app.db.models.service import Service
        service = db.query(Service).filter(Service.id == visit_data['service_id']).first()
        if service:
            logger.info(f"create_visit: found service id={service.id}, name={service.name}, price={service.price}, duration={service.duration_minutes}")
            
            # Применяем цену, если она не указана в визите (None или отсутствует)
            price_in_data = visit_data.get('price')
            if (price_in_data is None or price_in_data == 0) and service.price is not None:
                visit_data['price'] = service.price
                logger.info(f"create_visit: auto-applied price={service.price} from service (was: {price_in_data})")
            else:
                logger.info(f"create_visit: price not auto-applied (price_in_data={price_in_data}, service.price={service.price})")
            
            # Применяем длительность (date_end), если она не указана и есть duration_minutes
            date_end_in_data = visit_data.get('date_end')
            date_start = visit_data.get('date_start')
            
            if date_end_in_data is None and service.duration_minutes and date_start:
                from datetime import timedelta, datetime, timezone
                
                logger.info(f"create_visit: will auto-apply date_end, date_start={date_start}, type={type(date_start)}, duration={service.duration_minutes}")
                
                # Обрабатываем разные форматы date_start
                if isinstance(date_start, str):
                    # Парсим строку с учетом timezone
                    if date_start.endswith('Z'):
                        date_start = datetime.fromisoformat(date_start.replace('Z', '+00:00'))
                    elif '+' in date_start or date_start.count('-') > 2:
                        # Есть timezone в строке
                        date_start = datetime.fromisoformat(date_start)
                    else:
                        # Нет timezone, парсим как naive datetime
                        date_start = datetime.fromisoformat(date_start)
                    logger.info(f"create_visit: parsed date_start string to datetime: {date_start}, tzinfo={date_start.tzinfo}")
                elif isinstance(date_start, datetime):
                    # date_start уже является datetime объектом
                    logger.info(f"create_visit: date_start is already datetime: {date_start}, tzinfo={date_start.tzinfo}")
                
                # Вычисляем date_end
                date_end = date_start + timedelta(minutes=service.duration_minutes)
                
                # Убеждаемся, что date_end имеет тот же формат (timezone-aware или naive), что и date_start
                # Если date_start был naive, делаем date_end тоже naive
                if date_start.tzinfo is None:
                    # date_start naive - делаем date_end тоже naive (если он стал aware)
                    if date_end.tzinfo is not None:
                        date_end = date_end.replace(tzinfo=None)
                    visit_data['date_end'] = date_end
                else:
                    # date_start aware - оставляем date_end как есть (он уже aware)
                    visit_data['date_end'] = date_end
                
                logger.info(f"create_visit: auto-applied date_end={visit_data['date_end']} (type={type(visit_data['date_end'])}, tzinfo={visit_data['date_end'].tzinfo}) from service duration={service.duration_minutes} minutes")
            else:
                logger.info(f"create_visit: date_end not auto-applied (date_end_in_data={date_end_in_data}, service.duration_minutes={service.duration_minutes}, date_start={date_start})")
            
            # Применяем название услуги, если оно не указано
            service_name_in_data = visit_data.get('service_name')
            if not service_name_in_data and service.name:
                visit_data['service_name'] = service.name
                logger.info(f"create_visit: auto-applied service_name={service.name} from service")
            else:
                logger.info(f"create_visit: service_name not auto-applied (service_name_in_data={service_name_in_data}, service.name={service.name})")
        else:
            logger.warning(f"create_visit: service with id={visit_data.get('service_id')} not found")
    
    logger.info(f"create_visit: final visit_data before creating Visit: price={visit_data.get('price')}, date_end={visit_data.get('date_end')}, service_name={visit_data.get('service_name')}")
    
    # Проверяем наличие колонки business_id в таблице visits
    from sqlalchemy import inspect
    inspector = inspect(db.bind)
    has_business_id_column = False
    try:
        columns = [col['name'] for col in inspector.get_columns('visits')]
        has_business_id_column = 'business_id' in columns
    except Exception as e:
        logger.warning(f"create_visit: error checking columns: {str(e)}")
    
    # Set business_id if provided and column exists, otherwise try to get it from specialist
    if has_business_id_column:
        if business_id is None:
            # Try to get business_id from specialist
            from app.db.models.user import User
            specialist = db.query(User).filter(User.id == visit_data.get('specialist_id')).first()
            if specialist and hasattr(specialist, 'business_id') and specialist.business_id:
                business_id = specialist.business_id
                logger.info(f"create_visit: got business_id={business_id} from specialist_id={visit_data.get('specialist_id')}")
        
        if business_id:
            visit_data['business_id'] = business_id
            logger.info(f"create_visit: setting business_id={business_id}")
    else:
        # Если колонки нет, убираем business_id из данных
        visit_data.pop('business_id', None)
        logger.info("create_visit: business_id column not found, removing from visit_data")
    
    try:
        visit = Visit(**visit_data)
        db.add(visit)
        db.commit()
        db.refresh(visit)
        logger.info(f"create_visit: created visit id={visit.id}, client_id={visit.client_id}, specialist_id={visit.specialist_id}, service_id={getattr(visit, 'service_id', None)}, price={getattr(visit, 'price', None)}, date_end={getattr(visit, 'date_end', None)}, service_name={getattr(visit, 'service_name', None)}")
        return visit
    except Exception as e:
        logger.error(f"create_visit: error creating visit: {str(e)}", exc_info=True)
        db.rollback()
        raise


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



