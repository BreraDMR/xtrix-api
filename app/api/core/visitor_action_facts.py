"""Read-only action hints from an already authorized immutable visit projection."""
from dataclasses import dataclass
from datetime import datetime

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.api.core import subscription_access
from app.api.core.visitor_booking_policy import (
    BookingManageEligibility,
    BookingManageFacts,
    booking_manage_eligibility,
)
from app.api.crud import crud_public_booking


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
