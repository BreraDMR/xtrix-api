from typing import Optional, Tuple, List
from sqlalchemy.orm import Session, joinedload
from app.api.core.pagination import PaginationParams
from app.api.core.soft_delete import filter_active
from app.db.models.service import Service
from app.db.models.service_specialist import ServiceSpecialist
from app.schemas.service import ServiceCreate, ServiceUpdate
def create_service(db: Session, service_in: ServiceCreate, business_id: Optional[int] = None) -> Service:
    from app.api.core.logger import get_logger
    logger = get_logger("services")
    service_data = service_in.model_dump()
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
    update_data = update.model_dump(exclude_unset=True)
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
    from sqlalchemy import or_, select

    # Scope rules. There are no "shared" services: business_id IS NULL just means a solo
    # specialist's service, and those used to show up for every business and every specialist.
    #   business_id          -> services of that business, plus ones linked to its employees
    #   specialist_id only   -> that specialist's own services (owned or linked)
    #   neither              -> no scope (only the platform admin gets here)
    q = db.query(Service).options(joinedload(Service.specialist))
    if not include_deleted:
        q = filter_active(q)
    if not include_archived:
        q = q.filter(Service.archived_at.is_(None))
    linked = lambda ids: Service.id.in_(  # noqa: E731
        select(ServiceSpecialist.service_id).where(ServiceSpecialist.specialist_id.in_(ids))
    )
    if business_id is not None:
        conditions = [Service.business_id == business_id]
        if employee_specialist_ids:
            conditions += [Service.specialist_id.in_(employee_specialist_ids), linked(employee_specialist_ids)]
        q = q.filter(or_(*conditions))
        if specialist_id:
            q = q.filter(or_(Service.specialist_id == specialist_id, linked([specialist_id])))
    elif specialist_id:
        q = q.filter(or_(Service.specialist_id == specialist_id, linked([specialist_id])))
    elif employee_specialist_ids is not None:
        # an owner without a business yet: only what their employees provide
        q = q.filter(or_(Service.specialist_id.in_(employee_specialist_ids), linked(employee_specialist_ids)))
    if is_active is not None:
        q = q.filter(Service.is_active == is_active)
    total = q.count()
    if pagination:
        q = q.offset(pagination.offset).limit(pagination.limit)
    return q.all(), total
