"""A client's phones and emails: normalizing, saving, finding clients by any of them.

`clients.contact_phone` / `contact_email` mirror the primary contact of each kind. Everything that
writes contacts goes through here so the two never drift apart. Cards written straight into
`clients` (old code paths, seed data) are still found by lookups through those columns.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Optional, Sequence

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Query, Session

from app.db.models.client import Client
from app.db.models.client_contact import MAX_EMAILS, MAX_PHONES, ClientContact
from app.db.models.visit import Visit

LIMITS = {"phone": MAX_PHONES, "email": MAX_EMAILS}
_PHONE_SEPARATORS = re.compile(r"[\s\-\(\)\./]")
_NON_DIGITS = re.compile(r"\D")


class ContactsError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def normalize_phone(value: Optional[str]) -> Optional[str]:
    """+digits for a number in international form, bare digits for anything else, None if empty."""
    if value is None:
        return None
    text = _PHONE_SEPARATORS.sub("", str(value).strip())
    digits = _NON_DIGITS.sub("", text)
    if not digits:
        return None
    return "+" + digits if text.startswith("+") else digits


def normalize_email(value: Optional[str]) -> Optional[str]:
    text = str(value).strip().lower() if value is not None else ""
    return text or None


def normalize(kind: str, value: Optional[str]) -> Optional[str]:
    return normalize_phone(value) if kind == "phone" else normalize_email(value)


def _phone_digits(value: Optional[str]) -> str:
    return _NON_DIGITS.sub("", value or "")


@dataclass
class ContactValue:
    kind: str
    value: str
    label: Optional[str] = None
    is_primary: bool = False
    notify: bool = True


def check_contacts(contacts: Sequence[ContactValue]) -> list[ContactValue]:
    """Validate a full contacts list and settle who is primary. Values must already be normalized."""
    seen: set[tuple[str, str]] = set()
    for item in contacts:
        key = (item.kind, item.value)
        if key in seen:
            raise ContactsError("duplicate_contact", f"The {item.kind} {item.value} is listed twice")
        seen.add(key)
    out = list(contacts)
    for kind, limit in LIMITS.items():
        mine = [c for c in out if c.kind == kind]
        if len(mine) > limit:
            raise ContactsError(f"too_many_{kind}s", f"A client can have at most {limit} {kind}s")
        primaries = [c for c in mine if c.is_primary]
        if len(primaries) > 1:
            raise ContactsError("multiple_primary", f"Only one {kind} can be primary")
        if mine and not primaries:
            # nobody picked one - the first in the list is it
            mine[0].is_primary = True
    return out


def _mirror_columns(client: Client) -> None:
    for kind, column in (("phone", "contact_phone"), ("email", "contact_email")):
        primary = next((c for c in client.contacts if c.kind == kind and c.is_primary), None)
        current = getattr(client, column)
        if primary is None:
            setattr(client, column, None)
        elif normalize(kind, current) != primary.value:
            setattr(client, column, primary.value)
        # same number written with spaces stays as it was typed


def set_contacts(db: Session, client: Client, contacts: Sequence[ContactValue]) -> None:
    """Make the card's contacts exactly this list. Rows that stay keep their id and created_at."""
    wanted = check_contacts(contacts)
    existing = {(c.kind, c.value): c for c in client.contacts}
    keep = {(c.kind, c.value) for c in wanted}
    for key, row in list(existing.items()):
        if key not in keep:
            client.contacts.remove(row)
    # clear primaries first, so moving "primary" from one row to another never has two at once
    for row in client.contacts:
        row.is_primary = False
    db.flush()
    for item in wanted:
        row = existing.get((item.kind, item.value))
        if row is None:
            row = ClientContact(kind=item.kind, value=item.value)
            client.contacts.append(row)
        row.label = item.label
        row.notify = item.notify
    db.flush()
    by_key = {(c.kind, c.value): c for c in client.contacts}
    for item in wanted:
        by_key[(item.kind, item.value)].is_primary = item.is_primary
    db.flush()
    _mirror_columns(client)


def sync_from_columns(db: Session, client: Client) -> None:
    """Old-style write: only contact_phone / contact_email were set. Make that the primary contact
    and keep the other numbers. Clearing the column drops the primary and the next one takes over."""
    rows = list(client.contacts)
    wanted: list[ContactValue] = []
    for kind, column in (("phone", "contact_phone"), ("email", "contact_email")):
        mine = [c for c in rows if c.kind == kind]
        target = normalize(kind, getattr(client, column))
        values = [
            ContactValue(kind, c.value, c.label, False, c.notify)
            for c in mine
            if not (c.is_primary and target is None)
        ]
        if target is not None:
            match = next((v for v in values if v.value == target), None)
            if match is None:
                old_primary = next((c for c in mine if c.is_primary), None)
                if old_primary is not None:
                    # the primary number was changed, not a new one added
                    values = [v for v in values if v.value != old_primary.value]
                    match = ContactValue(kind, target, old_primary.label, True, old_primary.notify)
                else:
                    match = ContactValue(kind, target, None, True, True)
                values.insert(0, match)
                if len(values) > LIMITS[kind]:
                    values = values[: LIMITS[kind]]
            match.is_primary = True
            values.sort(key=lambda v: not v.is_primary)
        wanted.extend(values)
    set_contacts(db, client, wanted)


def add_contacts(db: Session, client: Client, kind: str, values: Iterable[Optional[str]]) -> None:
    """Append extra numbers (import's phone2..phone5). Known ones and ones over the limit are skipped."""
    current = [ContactValue(c.kind, c.value, c.label, c.is_primary, c.notify) for c in client.contacts]
    taken = {c.value for c in current if c.kind == kind}
    count = len(taken)
    for raw in values:
        value = normalize(kind, raw)
        if value is None or value in taken or count >= LIMITS[kind]:
            continue
        current.append(ContactValue(kind, value))
        taken.add(value)
        count += 1
    set_contacts(db, client, current)


def notify_value(client: Optional[Client], kind: str) -> Optional[str]:
    """Where messages of this kind go: the primary if it's allowed, else the first that is.
    Cards without contact rows fall back to the old column."""
    if client is None:
        return None
    # some callers hand in a plain stand-in with just the columns, no contact rows
    rows = [c for c in (getattr(client, "contacts", None) or []) if c.kind == kind]
    if not rows:
        column = getattr(client, "contact_phone" if kind == "phone" else "contact_email", None)
        return (column or "").strip() or None
    rows.sort(key=lambda c: (not c.is_primary, c.id or 0))
    return next((c.value for c in rows if c.notify), None)


def notify_phone(client: Optional[Client]) -> Optional[str]:
    return notify_value(client, "phone")


def notify_email(client: Optional[Client]) -> Optional[str]:
    return notify_value(client, "email")


def client_emails(client: Client) -> set[str]:
    emails = {c.value for c in client.contacts if c.kind == "email"}
    if client.contact_email:
        emails.add(normalize_email(client.contact_email))
    return emails


# --- lookups ------------------------------------------------------------------------

def _phone_column_digits():
    expression = Client.contact_phone
    for character in (" ", "-", "(", ")", ".", "/", "+"):
        expression = func.replace(expression, character, "")
    return expression


def _phone_keys(phone: str) -> list[str]:
    digits = _phone_digits(phone)
    return ["+" + digits, digits]


def contact_filter(phone: Optional[str] = None, email: Optional[str] = None):
    """A WHERE clause: the client has this phone or this email among any of its contacts."""
    clauses = []
    digits = _phone_digits(phone)
    if digits:
        clauses.append(and_(Client.contact_phone.isnot(None), _phone_column_digits() == digits))
        clauses.append(Client.id.in_(
            select(ClientContact.client_id).where(ClientContact.kind == "phone", ClientContact.value.in_(_phone_keys(phone)))
        ))
    mail = normalize_email(email)
    if mail:
        clauses.append(func.lower(func.trim(Client.contact_email)) == mail)
        clauses.append(Client.id.in_(
            select(ClientContact.client_id).where(ClientContact.kind == "email", ClientContact.value == mail)
        ))
    return or_(*clauses) if clauses else None


def search_filter(pattern: str):
    """For the clients list search box: any contact containing the text."""
    return Client.id.in_(
        select(ClientContact.client_id).where(ClientContact.value.ilike(pattern, escape="\\"))
    )


def _matches(client: Client, kind: str, value: str) -> tuple[bool, bool]:
    """(matched at all, matched the primary one)."""
    if kind == "phone":
        digits = _phone_digits(value)
        if client.contact_phone and _phone_digits(client.contact_phone) == digits:
            return True, True
        rows = [c for c in client.contacts if c.kind == "phone" and _phone_digits(c.value) == digits]
    else:
        if client.contact_email and normalize_email(client.contact_email) == value:
            return True, True
        rows = [c for c in client.contacts if c.kind == "email" and c.value == value]
    return bool(rows), any(c.is_primary for c in rows)


def find_clients(
    db: Session,
    query: Query,
    phone: Optional[str] = None,
    email: Optional[str] = None,
) -> list[Client]:
    """Clients from `query` (already scoped to one salon or one user) with this phone or email,
    best match first.

    A family can share one number, so several cards may match. The order is fixed: a card that
    matches both the phone and the email, then one where it's the primary contact, then the one
    with the latest visit, then the oldest card.
    """
    clause = contact_filter(phone, email)
    if clause is None:
        return []
    candidates = list({c.id: c for c in query.filter(clause).all()}.values())
    if not candidates:
        return []
    ids = [c.id for c in candidates]
    last_visit = dict(
        db.query(Visit.client_id, func.max(Visit.date_start))
        .filter(Visit.client_id.in_(ids), Visit.deleted_at.is_(None))
        .group_by(Visit.client_id)
        .all()
    )
    mail = normalize_email(email)

    def rank(client: Client):
        phone_hit = _matches(client, "phone", phone) if _phone_digits(phone) else (False, False)
        email_hit = _matches(client, "email", mail) if mail else (False, False)
        both = phone_hit[0] and email_hit[0]
        primary = phone_hit[1] or email_hit[1]
        seen = last_visit.get(client.id)
        return (not both, not primary, seen is None, -(seen.timestamp()) if seen else 0, client.id)

    return sorted(candidates, key=rank)


def find_client(db: Session, query: Query, phone: Optional[str] = None,
                email: Optional[str] = None) -> Optional[Client]:
    found = find_clients(db, query, phone, email)
    return found[0] if found else None


def snapshot(client: Client) -> list[tuple]:
    return sorted((c.kind, c.value, c.label, bool(c.is_primary), bool(c.notify)) for c in client.contacts)


def apply_payload(db: Session, client: Client, contacts=None) -> None:
    """After a create/update: take the sent list as the truth, or else follow the old columns."""
    if contacts is not None:
        set_contacts(db, client, [
            ContactValue(c.kind, c.value, c.label, c.is_primary, c.notify) for c in contacts
        ])
    else:
        sync_from_columns(db, client)


def find_booking_client(db: Session, query: Query, phone: Optional[str],
                        email: Optional[str]) -> Optional[Client]:
    """The card a guest's booking (or waitlist entry) belongs to.

    The email decides first - it's personal, a phone is often shared in a family. With no card for
    the email, the phone only picks a card that has no email of its own: a card with a different
    email is somebody else. Nothing the guest typed is ever added to an existing card, otherwise a
    stranger who knows a number could plant their email on it and sign in to that client's portal.
    """
    if normalize_email(email):
        found = find_client(db, query, email=email)
        if found is not None or not _phone_digits(phone):
            return found
        return next((c for c in find_clients(db, query, phone=phone) if not client_emails(c)), None)
    if _phone_digits(phone):
        return find_client(db, query, phone=phone)
    return None
