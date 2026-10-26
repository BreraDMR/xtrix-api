"""Fresh CRM authority for one visitor invitation, without billing or token issuance."""
from dataclasses import dataclass, field

from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.api.core import permissions
from app.db.models.business import Business
from app.db.models.client import Client
from app.db.models.client_business_relation import ClientBusinessRelation
from app.db.models.client_specialist_relation import ClientSpecialistRelation
from app.db.models.employee import Employee
from app.db.models.user import User


class InviteAuthorizationBusy(ValueError):
    """A concurrent permission writer won; caller must whole-rollback and retry."""


class InviteAuthorizationDenied(ValueError):
    """This issuer cannot grant access to this fixed business/client pair."""


@dataclass(frozen=True)
class ClientInviteAuthority:
    issuer_user_id: int = field(repr=False)
    business_id: int = field(repr=False)
    client_id: int = field(repr=False)


def authorize_client_invite(db: Session, *, issuer_user_id: int,
                            business_id: int, client_id: int) -> ClientInviteAuthority:
    """Check existing edit/client scope, taking only NOWAIT authorization locks.

    Caller must already hold this Client row FOR NO KEY UPDATE until commit. Visitor
    accept takes its Account lock before Client, and rechecks its exact opaque session
    separately. issuer_user_id is selected by the caller, not authentication proof.
    Issue must revalidate the same CRM credential/revocation after waits; accept
    checks current issuer rights without an old issuer JWT. No auth or payment proof.
    Re-run after the last blocking claim/grant write; do not change authorization rows
    in this transaction. Every error requires whole rollback, including 55P03. No
    commit, rollback, billing, invitation, visitor grant or outgoing mail is performed.
    """
    if any(isinstance(value, bool) or not isinstance(value, int) or value <= 0
           for value in (issuer_user_id, business_id, client_id)):
        raise InviteAuthorizationDenied('Invitation authority is not available')
    try:
        with db.no_autoflush:
            # UPDATE also blocks new Employee FK rows; SHARE leaves that absence unprotected.
            issuer = db.query(User).filter(User.id == issuer_user_id, User.deleted_at.is_(None)
                ).populate_existing().with_for_update(nowait=True).one_or_none()
            if issuer is None:
                raise InviteAuthorizationDenied('Invitation authority is not available')
            # Include inactive rows: a concurrent reactivation must not bypass the guard.
            staff_rows = db.query(Employee).filter(Employee.employee_user_id == issuer_user_id
                ).order_by(Employee.id).populate_existing().with_for_update(read=True, nowait=True).all()
            active_staff = [row for row in staff_rows if row.is_active]
            if len(active_staff) > 1:
                raise InviteAuthorizationDenied('Invitation authority is not available')
            staff = active_staff[0] if active_staff and issuer.role in ('specialist', 'employee') else None
            if staff is not None:
                owner = db.query(User).filter(User.id == staff.business_owner_id, User.deleted_at.is_(None)
                    ).populate_existing().with_for_update(read=True, nowait=True).one_or_none()
                if owner is None:
                    raise InviteAuthorizationDenied('Invitation authority is not available')
            business = db.query(Business.id).filter(Business.id == business_id, Business.deleted_at.is_(None)
                ).with_for_update(read=True, nowait=True).scalar()
            client = db.query(Client.id, Client.owner_id).filter(Client.id == client_id, Client.deleted_at.is_(None)
                ).one_or_none()
            membership = db.query(ClientBusinessRelation.id).filter(
                ClientBusinessRelation.client_id == client_id, ClientBusinessRelation.business_id == business_id
                ).with_for_update(read=True, nowait=True).scalar()
            if business is None or client is None or membership is None:
                raise InviteAuthorizationDenied('Invitation authority is not available')
            if staff is None and issuer.role == 'specialist' and client.owner_id != issuer.id:
                link = db.query(ClientSpecialistRelation.id).filter(
                    ClientSpecialistRelation.client_id == client_id,
                    ClientSpecialistRelation.specialist_id == issuer.id,
                    ClientSpecialistRelation.business_id == business_id,
                ).with_for_update(read=True, nowait=True).scalar()
                if link is None:
                    raise InviteAuthorizationDenied('Invitation authority is not available')
            try:
                allowed = (permissions.can_edit_clients(issuer, db)
                    and permissions.check_client_access(db, issuer, client_id)
                    and permissions.get_user_business_id(issuer, db) == business_id)
            except ValueError:
                raise InviteAuthorizationDenied('Invitation authority is not available') from None
            if not allowed:
                raise InviteAuthorizationDenied('Invitation authority is not available')
            return ClientInviteAuthority(issuer.id, business_id, client_id)
    except DBAPIError as exc:
        if getattr(exc.orig, 'pgcode', None) == '55P03':
            raise InviteAuthorizationBusy('Invitation authority is busy; retry after rollback') from None
        raise
