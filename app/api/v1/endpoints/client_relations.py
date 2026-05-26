from typing import List
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.api import deps
from app.api.core.i18n import _, localize_response
from app.api.core.logger import get_logger
from app.api.crud import crud_client_specialist_relation
from app.db.models.user import User
from app.schemas.client_specialist_relation import (
    ClientSpecialistRelationCreate,
    ClientSpecialistRelationOut
)
router = APIRouter()
logger = get_logger("client_relations")
@router.post(
    "/",
    response_model=ClientSpecialistRelationOut,
    summary="Link client to specialist",
    description="Create a link between a client and a specialist. Allows a client to be accessed by multiple specialists.",
)
def create_client_specialist_relation(
    relation: ClientSpecialistRelationCreate,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.role_required("admin", "specialist")),
    locale: str = Depends(deps.get_locale),
):
    if current_user.role != "admin" and relation.specialist_id != current_user.id:
        raise HTTPException(status_code=403, detail=_("not_allowed", locale))
    from app.api.crud import crud_client
    client = crud_client.get_client(db, relation.client_id)
    if not client:
        raise HTTPException(status_code=404, detail=_("client_not_found", locale))
    if current_user.role != "admin":
        has_access = False
        if client.owner_id == current_user.id:
            has_access = True
        else:
            existing_relation = crud_client_specialist_relation.get_relation(
                db, relation.client_id, current_user.id
            )
            has_access = existing_relation is not None
        if not has_access:
            raise HTTPException(status_code=403, detail=_("not_allowed", locale))
    result = crud_client_specialist_relation.create_relation(db, relation)
    logger.info(f"Client-specialist relation created: client_id={relation.client_id}, specialist_id={relation.specialist_id}")
    return localize_response(result.dict() if hasattr(result, 'dict') else result, locale)
@router.get(
    "/clients/{client_id}/specialists",
    response_model=List[ClientSpecialistRelationOut],
    summary="Get specialists linked to a client",
)
def get_client_specialists(
    client_id: int,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.role_required("admin", "specialist")),
    locale: str = Depends(deps.get_locale),
):
    from app.api.crud import crud_client
    client = crud_client.get_client(db, client_id)
    if not client:
        raise HTTPException(status_code=404, detail=_("client_not_found", locale))
    if current_user.role != "admin":
        has_access = (
            client.owner_id == current_user.id or
            crud_client_specialist_relation.get_relation(db, client_id, current_user.id) is not None
        )
        if not has_access:
            raise HTTPException(status_code=403, detail=_("not_allowed", locale))
    relations = crud_client_specialist_relation.get_client_specialists(db, client_id)
    return [localize_response(r.dict() if hasattr(r, 'dict') else r, locale) for r in relations]
@router.get(
    "/specialists/{specialist_id}/clients",
    response_model=List[ClientSpecialistRelationOut],
    summary="Get clients linked to a specialist",
)
def get_specialist_clients(
    specialist_id: int,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.role_required("admin", "specialist")),
    locale: str = Depends(deps.get_locale),
):
    if current_user.role != "admin" and specialist_id != current_user.id:
        raise HTTPException(status_code=403, detail=_("not_allowed", locale))
    relations = crud_client_specialist_relation.get_specialist_clients(db, specialist_id)
    return [localize_response(r.dict() if hasattr(r, 'dict') else r, locale) for r in relations]
@router.delete(
    "/clients/{client_id}/specialists/{specialist_id}",
    summary="Unlink client from specialist",
)
def delete_client_specialist_relation(
    client_id: int,
    specialist_id: int,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.role_required("admin", "specialist")),
    locale: str = Depends(deps.get_locale),
):
    if current_user.role != "admin" and specialist_id != current_user.id:
        raise HTTPException(status_code=403, detail=_("not_allowed", locale))
    success = crud_client_specialist_relation.delete_relation(db, client_id, specialist_id)
    if not success:
        raise HTTPException(status_code=404, detail=_("relation_not_found", locale))
    logger.info(f"Client-specialist relation deleted: client_id={client_id}, specialist_id={specialist_id}")
    return {"message": _("relation_deleted", locale)}
