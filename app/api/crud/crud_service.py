from typing import Optional, Tuple, List
from sqlalchemy.orm import Session, joinedload
from app.api.core.pagination import PaginationParams
from app.api.core.soft_delete import filter_active, soft_delete
from app.db.models.service import Service
from app.db.models.service_specialist import ServiceSpecialist
from app.schemas.service import ServiceCreate, ServiceUpdate
def create_service(db: Session, service_in: ServiceCreate, business_id: Optional[int] = None) -> Service:
    from app.api.core.logger import get_logger
    logger = get_logger("services")
    service_data = service_in.dict()
    logger.info(f"create_service: service_data={service_data}, business_id={business_id}")
    from sqlalchemy import inspect
    inspector = inspect(db.bind)
    has_business_id_column = False
    has_category_column = False
    try:
        columns = [col['name'] for col in inspector.get_columns('services')]
        has_business_id_column = 'business_id' in columns
        has_category_column = 'category' in columns
    except:
        pass
    if has_category_column and not service_data.get('category') and service_data.get('specialist_id'):
        from app.db.models.user import User
        from app.db.models.specialist_type import SpecialistType, get_category_for_type
        specialist = db.query(User).filter(User.id == service_data['specialist_id']).first()
        if specialist and specialist.specialist_type:
            try:
                specialist_type_enum = SpecialistType(specialist.specialist_type)
                category = get_category_for_type(specialist_type_enum)
                service_data['category'] = category.value
                logger.info(f"create_service: auto-determined category={category.value} from specialist_type={specialist.specialist_type}")
            except (ValueError, AttributeError):
                pass
    if has_business_id_column:
        if business_id is None and service_data.get('specialist_id'):
            from app.db.models.user import User
            specialist = db.query(User).filter(User.id == service_data['specialist_id']).first()
            if specialist and hasattr(specialist, 'business_id') and specialist.business_id:
                business_id = specialist.business_id
                logger.info(f"create_service: got business_id={business_id} from specialist_id={service_data['specialist_id']}")
        service_data['business_id'] = business_id
        logger.info(f"create_service: setting business_id={business_id} in service_data")
    else:
        service_data.pop('business_id', None)
    if not has_category_column:
        service_data.pop('category', None)
    specialist_ids = service_data.pop('specialist_ids', None)
    service = Service(**service_data)
    db.add(service)
    db.commit()
    db.refresh(service)
    if specialist_ids:
        all_specialist_ids = set(specialist_ids)
        if service.specialist_id not in all_specialist_ids:
            all_specialist_ids.add(service.specialist_id)
        for spec_id in all_specialist_ids:
            service_spec = ServiceSpecialist(service_id=service.id, specialist_id=spec_id)
            db.add(service_spec)
        db.commit()
    else:
        service_spec = ServiceSpecialist(service_id=service.id, specialist_id=service.specialist_id)
        db.add(service_spec)
        db.commit()
    logger.info(f"create_service: created service id={service.id}, specialist_id={service.specialist_id}, business_id={getattr(service, 'business_id', None)}, category={getattr(service, 'category', None)}")
    return service
def update_service(db: Session, service: Service, update: ServiceUpdate) -> Service:
    update_data = update.dict(exclude_unset=True)
    specialist_ids = update_data.pop('specialist_ids', None)
    for field, value in update_data.items():
        setattr(service, field, value)
    db.add(service)
    db.commit()
    db.refresh(service)
    if specialist_ids is not None:
        db.query(ServiceSpecialist).filter(ServiceSpecialist.service_id == service.id).delete()
        all_specialist_ids = set(specialist_ids)
        if service.specialist_id not in all_specialist_ids:
            all_specialist_ids.add(service.specialist_id)
        for spec_id in all_specialist_ids:
            service_spec = ServiceSpecialist(service_id=service.id, specialist_id=spec_id)
            db.add(service_spec)
        db.commit()
    return service
def get_service(db: Session, service_id: int, include_deleted: bool = False) -> Optional[Service]:
    query = db.query(Service).options(
        joinedload(Service.specialist),
        joinedload(Service.service_specialists).joinedload(ServiceSpecialist.specialist)
    )
    if not include_deleted:
        query = filter_active(query)
    return query.filter(Service.id == service_id).first()
def list_services(
    db: Session,
    specialist_id: Optional[int] = None,
    business_id: Optional[int] = None,  
    employee_specialist_ids: Optional[List[int]] = None,  
    is_active: Optional[bool] = None,  
    pagination: Optional[PaginationParams] = None,
    include_deleted: bool = False,
    include_archived: bool = False,
) -> Tuple[list[Service], int]:
    from app.api.core.logger import get_logger
    logger = get_logger("services")
    from sqlalchemy import inspect
    inspector = inspect(db.bind)
    has_business_id_column = False
    try:
        columns = [col['name'] for col in inspector.get_columns('services')]
        has_business_id_column = 'business_id' in columns
    except:
        pass
    logger.info(f"list_services: specialist_id={specialist_id}, business_id={business_id}, has_business_id_column={has_business_id_column}, is_active={is_active}")
    if not has_business_id_column:
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
        items = []
        for row in result:
            service = Service()
            for key, value in row._mapping.items():
                setattr(service, key, value)
            if service.specialist_id:
                from app.db.models.user import User
                service.specialist = db.query(User).filter(User.id == service.specialist_id).first()
            items.append(service)
        return items, total
    q = db.query(Service).options(joinedload(Service.specialist))
    logger.info(f"list_services: include_deleted={include_deleted}, include_archived={include_archived}")
    if not include_deleted:
        q = filter_active(q)
        logger.info("list_services: applied filter_active (deleted_at IS NULL)")
    if not include_archived:
        q = q.filter(Service.archived_at.is_(None))
        logger.info("list_services: applied archived_at IS NULL filter")
    if has_business_id_column:
        from sqlalchemy import or_
        business_conditions = []
        if business_id is not None:
            business_conditions.append(Service.business_id == business_id)
            business_conditions.append(Service.business_id.is_(None))  
            logger.info(f"list_services: filtering by (business_id={business_id} OR business_id IS NULL)")
        if employee_specialist_ids and len(employee_specialist_ids) > 0:
            from app.db.models.service_specialist import ServiceSpecialist
            from sqlalchemy import select
            service_ids_subquery = select(ServiceSpecialist.service_id).where(
                ServiceSpecialist.specialist_id.in_(employee_specialist_ids)
            ).distinct()
            business_conditions.append(Service.id.in_(service_ids_subquery))
            business_conditions.append(Service.specialist_id.in_(employee_specialist_ids))
            logger.info(f"list_services: including services from employee specialists: {employee_specialist_ids}")
        if business_conditions:
            q = q.filter(or_(*business_conditions))
        elif business_id is None:
            if specialist_id:
                q = q.filter(or_(
                    Service.business_id.is_(None),  
                    Service.specialist_id == specialist_id  
                ))
            else:
                q = q.filter(Service.business_id.is_(None))
                logger.info(f"list_services: filtering by business_id IS NULL (no specialist_id)")
    else:
        if employee_specialist_ids and len(employee_specialist_ids) > 0:
            from app.db.models.service_specialist import ServiceSpecialist
            from sqlalchemy import or_, select
            service_ids_subquery = select(ServiceSpecialist.service_id).where(
                ServiceSpecialist.specialist_id.in_(employee_specialist_ids)
            ).distinct()
            q = q.filter(or_(
                Service.id.in_(service_ids_subquery),
                Service.specialist_id.in_(employee_specialist_ids)
            ))
            logger.info(f"list_services: filtering by employee_specialist_ids: {employee_specialist_ids}")
    if specialist_id and (business_id is not None or not has_business_id_column) and not employee_specialist_ids:
        q = q.filter(Service.specialist_id == specialist_id)
        logger.info(f"list_services: additional filter by specialist_id={specialist_id}")
    if is_active is not None:
        q = q.filter(Service.is_active == is_active)
    try:
        logger.info(f"list_services: SQL query: {str(q.statement)}")
        total = q.count()
        logger.info(f"list_services: count result: {total}")
    except Exception as e:
        if 'business_id' in str(e).lower() or 'does not exist' in str(e).lower():
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
    if pagination:
        q = q.offset(pagination.offset).limit(pagination.limit)
    try:
        items = q.all()
        logger.info(f"list_services: found {len(items)} services, total={total}")
    except Exception as e:
        logger.error(f"list_services: error getting items: {str(e)}")
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
            items = []
            for row in result:
                service = Service()
                for key, value in row._mapping.items():
                    setattr(service, key, value)
                items.append(service)
        else:
            raise
    return items, total
