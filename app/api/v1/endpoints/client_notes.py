from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api import deps
from app.api.core.i18n import _
from app.api.core.permissions import can_edit_clients, check_client_access, require_business_id
from app.api.crud import crud_client, crud_client_note
from app.db.models.client_business_relation import ClientBusinessRelation
from app.db.models.client_note import ClientNote
from app.db.models.user import User
from app.schemas.client_profile import (
    ClientNoteCreate,
    ClientNoteOut,
    ClientNoteUpdate,
    ClientProfileOut,
    ClientProfileUpdate,
)

router = APIRouter()

STAFF_ROLES = ("admin", "business_owner", "specialist", "employee")


def _client_or_404(db: Session, current_user: User, client_id: int, locale: str):
    client = crud_client.get_client(db, client_id)
    if not client:
        raise HTTPException(status_code=404, detail=_("client_not_found", locale))
    if not check_client_access(db, current_user, client_id):
        raise HTTPException(status_code=403, detail=_("not_allowed", locale))
    return client


def _require_edit(db: Session, current_user: User, locale: str) -> None:
    if not can_edit_clients(current_user, db):
        raise HTTPException(status_code=403, detail=_("not_allowed", locale))


def _note_out(note: ClientNote) -> ClientNoteOut:
    out = ClientNoteOut.model_validate(note)
    out.author_name = note.author.full_name if note.author else None
    return out


def _note_or_404(
    db: Session, note_id: int, client_id: int, business_id: int, locale: str
) -> ClientNote:
    note = crud_client_note.get_note(db, note_id, client_id, business_id)
    if not note:
        raise HTTPException(status_code=404, detail=_("client_note_not_found", locale))
    return note


def _relation(db: Session, client_id: int, business_id: int) -> ClientBusinessRelation | None:
    return db.query(ClientBusinessRelation).filter(
        ClientBusinessRelation.client_id == client_id,
        ClientBusinessRelation.business_id == business_id,
    ).first()


def _profile_out(client_id: int, relation: ClientBusinessRelation | None) -> ClientProfileOut:
    tags = relation.tags if relation and isinstance(relation.tags, list) else []
    return ClientProfileOut(
        client_id=client_id,
        tags=[str(tag) for tag in tags],
        preferences=relation.preferences if relation else None,
    )


@router.get("/{client_id}/notes", response_model=list[ClientNoteOut])
def list_client_notes(
    client_id: int,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.role_required(*STAFF_ROLES)),
    locale: str = Depends(deps.get_locale),
):
    _client_or_404(db, current_user, client_id, locale)
    business_id = require_business_id(current_user, db, locale)
    return [
        _note_out(note)
        for note in crud_client_note.list_notes(db, client_id, business_id)
    ]


@router.post("/{client_id}/notes", response_model=ClientNoteOut, status_code=201)
def create_client_note(
    client_id: int,
    payload: ClientNoteCreate,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.role_required(*STAFF_ROLES)),
    locale: str = Depends(deps.get_locale),
):
    _client_or_404(db, current_user, client_id, locale)
    _require_edit(db, current_user, locale)
    business_id = require_business_id(current_user, db, locale)
    note = crud_client_note.create_note(db, client_id, business_id, current_user.id, payload)
    return _note_out(note)


@router.put("/{client_id}/notes/{note_id}", response_model=ClientNoteOut)
def update_client_note(
    client_id: int,
    note_id: int,
    payload: ClientNoteUpdate,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.role_required(*STAFF_ROLES)),
    locale: str = Depends(deps.get_locale),
):
    _client_or_404(db, current_user, client_id, locale)
    _require_edit(db, current_user, locale)
    business_id = require_business_id(current_user, db, locale)
    note = _note_or_404(db, note_id, client_id, business_id, locale)
    return _note_out(crud_client_note.update_note(db, note, payload))


@router.delete("/{client_id}/notes/{note_id}")
def delete_client_note(
    client_id: int,
    note_id: int,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.role_required(*STAFF_ROLES)),
    locale: str = Depends(deps.get_locale),
):
    _client_or_404(db, current_user, client_id, locale)
    _require_edit(db, current_user, locale)
    business_id = require_business_id(current_user, db, locale)
    note = _note_or_404(db, note_id, client_id, business_id, locale)
    crud_client_note.delete_note(db, note)
    return {"ok": True}


@router.get("/{client_id}/profile", response_model=ClientProfileOut)
def get_client_profile(
    client_id: int,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.role_required(*STAFF_ROLES)),
    locale: str = Depends(deps.get_locale),
):
    _client_or_404(db, current_user, client_id, locale)
    business_id = require_business_id(current_user, db, locale)
    return _profile_out(client_id, _relation(db, client_id, business_id))


@router.put("/{client_id}/profile", response_model=ClientProfileOut)
def update_client_profile(
    client_id: int,
    payload: ClientProfileUpdate,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.role_required(*STAFF_ROLES)),
    locale: str = Depends(deps.get_locale),
):
    _client_or_404(db, current_user, client_id, locale)
    _require_edit(db, current_user, locale)
    business_id = require_business_id(current_user, db, locale)
    relation = _relation(db, client_id, business_id)
    changes = payload.model_dump(exclude_unset=True)
    if relation is None:
        relation = ClientBusinessRelation(client_id=client_id, business_id=business_id)
        db.add(relation)
    for field, value in changes.items():
        setattr(relation, field, value)
    db.commit()
    db.refresh(relation)
    return _profile_out(client_id, relation)
