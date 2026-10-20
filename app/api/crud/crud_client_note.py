from typing import List, Optional

from sqlalchemy.orm import Session, joinedload

from app.db.models.client_note import ClientNote
from app.schemas.client_profile import ClientNoteCreate, ClientNoteUpdate


def list_notes(db: Session, client_id: int, business_id: int) -> List[ClientNote]:
    return (
        db.query(ClientNote)
        .options(joinedload(ClientNote.author))
        .filter(ClientNote.client_id == client_id, ClientNote.business_id == business_id)
        .order_by(ClientNote.created_at.desc(), ClientNote.id.desc())
        .all()
    )


def get_note(db: Session, note_id: int, client_id: int, business_id: int) -> Optional[ClientNote]:
    return (
        db.query(ClientNote)
        .options(joinedload(ClientNote.author))
        .filter(
            ClientNote.id == note_id,
            ClientNote.client_id == client_id,
            ClientNote.business_id == business_id,
        )
        .first()
    )


def create_note(
    db: Session,
    client_id: int,
    business_id: int,
    author_id: int,
    data: ClientNoteCreate,
) -> ClientNote:
    note = ClientNote(
        client_id=client_id,
        business_id=business_id,
        author_id=author_id,
        body=data.body,
    )
    db.add(note)
    db.commit()
    db.refresh(note)
    return note


def update_note(db: Session, note: ClientNote, data: ClientNoteUpdate) -> ClientNote:
    note.body = data.body
    db.add(note)
    db.commit()
    db.refresh(note)
    return note


def delete_note(db: Session, note: ClientNote) -> None:
    db.delete(note)
    db.commit()
