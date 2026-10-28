"""Read-only action hints from an already authorized immutable visit projection."""
from dataclasses import dataclass
from datetime import datetime

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.core import subscription_access, visitor_history
from app.api.core.visitor_booking_policy import (
    BookingManageEligibility,
    BookingManageFacts,
    booking_manage_eligibility,
)
from app.api.crud import crud_public_booking
from app.db.models.visit import Visit
from app.db.models.visit_item import VisitItem


@dataclass(frozen=True)
class ServiceMasterPair:
    service_id: int | None
    master_id: int | None


@dataclass(frozen=True)
class AuthorizedActionTarget:
    visit_id: int
    business_id: int
    status: str | None
    start: datetime | None
    service_master_pairs: tuple[ServiceMasterPair, ...]

    def __post_init__(self):
        # Keep the projection immutable even if a caller builds it from a list.
        object.__setattr__(self, "service_master_pairs", tuple(self.service_master_pairs))


def _positive_id(value: int | None) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _catalog_available(db: Session, target: AuthorizedActionTarget) -> bool:
    if not target.service_master_pairs:
        return False
    for pair in target.service_master_pairs:
        if not _positive_id(pair.service_id) or not _positive_id(pair.master_id):
            return False
        service = crud_public_booking.get_public_service(db, target.business_id, pair.service_id)
        if service is None:
            return False
        if pair.master_id not in crud_public_booking.public_service_master_ids(db, target.business_id, service):
            return False
    return True


def read_booking_manage_eligibility(
    db: Session, target: AuthorizedActionTarget, *, now: datetime,
) -> BookingManageEligibility:
    """Read billing/catalog hints without loading a Visit, Client or visitor identity.

    Caller obtained every target scalar, including ordered item pairs or the legacy
    service/master pair, from its exact-authorized projection. This target is not
    an authorization proof. No raw visit id is resolved here. Commands must repeat
    session/grant/scope/time/billing/catalog checks after their waits.

    Reads borrow the caller's connection and transaction, with a separate identity
    map so dirty or stale caller models are neither refreshed nor reused. The
    borrowed session has no pending writes; rollback_only means its close does not
    commit or roll back the parent's transaction. No nested transaction or second
    connection is opened. Caller no_autoflush also covers acquiring its connection.

    This is SELECT-only implementation, compatible with a PG read-only transaction;
    it does not change the caller's transaction mode. Unexpected errors propagate
    and the caller owns recovery. Only expected 402/403 billing denials become an
    opaque public reason. now controls temporal policy; existing billing helpers
    use their own current clock. Multiple SELECTs are a hint, not one immutable
    authorization/billing/catalog snapshot, a lock or a target-slot reservation.
    """
    writable = False
    catalog = False
    if _positive_id(target.visit_id) and _positive_id(target.business_id):
        with db.no_autoflush:
            with Session(bind=db.connection(), autoflush=False, join_transaction_mode="rollback_only") as reads:
                try:
                    subscription_access.enforce_business_access(reads, target.business_id, write=True)
                    writable = True
                except HTTPException as exc:
                    if exc.status_code not in (402, 403):
                        raise
                if writable:
                    catalog = _catalog_available(reads, target)
    return booking_manage_eligibility(
        BookingManageFacts(
            status=target.status, start=target.start,
            business_write_available=writable, reschedule_catalog_available=catalog,
        ),
        now=now,
    )


def get_authorized_action_target(db: Session, account_id: int, visit_id: int) -> AuthorizedActionTarget | None:
    """Read exact granted scope and stored item/legacy pairs in one SQL snapshot.

    account_id is supplied by a separately verified visitor session, never by body
    or query input. Do not reread the projected visit IDs through an ORM lookup.
    Every stored item participates regardless of its scheduling active flag; only
    visits without items use the parent service/master pair. No catalog prices,
    client records, internal notes or payment state are projected here.
    """
    scoped = visitor_history.visit_detail_query(account_id, visit_id).add_columns(
        Visit.service_id.label("parent_service_id"), Visit.specialist_id.label("parent_master_id"),
    ).subquery("authorized_action_visit")
    query = select(
        scoped.c.id, scoped.c.business_id, scoped.c.status, scoped.c.date_start,
        scoped.c.parent_service_id, scoped.c.parent_master_id,
        VisitItem.id.label("item_id"), VisitItem.service_id.label("item_service_id"),
        VisitItem.specialist_id.label("item_master_id"),
    ).select_from(scoped).outerjoin(VisitItem, VisitItem.visit_id == scoped.c.id).order_by(
        VisitItem.position.asc(), VisitItem.id.asc(),
    )
    with db.no_autoflush:
        rows = db.execute(query).mappings().all()
    if not rows:
        return None
    first = rows[0]
    pairs = tuple(ServiceMasterPair(row["item_service_id"], row["item_master_id"])
                  for row in rows if row["item_id"] is not None)
    if not pairs:
        pairs = (ServiceMasterPair(first["parent_service_id"], first["parent_master_id"]),)
    return AuthorizedActionTarget(
        visit_id=first["id"], business_id=first["business_id"], status=first["status"],
        start=first["date_start"], service_master_pairs=pairs,
    )


def get_visit_actions(db: Session, account_id: int, visit_id: int, *, now: datetime) -> BookingManageEligibility | None:
    """Reject an unavailable scope before consulting any business billing/catalog.

    The authorized projection is one statement. Later eligibility reads are only
    a hint; they do not promise a single snapshot or revocation linearization.
    """
    target = get_authorized_action_target(db, account_id, visit_id)
    return read_booking_manage_eligibility(db, target, now=now) if target is not None else None
