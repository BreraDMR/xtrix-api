"""Prepare one recipient-bound visitor invitation; delivery and commit stay with the caller."""
from dataclasses import dataclass, field
from datetime import datetime, timedelta
import hashlib
import secrets

from pydantic import EmailStr, TypeAdapter
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.core import permissions, subscription_access, visitor_invite_authorization
from app.api.core.clock import utcnow
from app.api.crud.crud_subscription import subscription_payer_id
from app.db.models.client import Client
from app.db.models.subscription import Subscription
from app.db.models.user import User
from app.db.models.visitor_access import VisitorClaim
from app.db.models.visitor_account import canonical_visitor_email

INVITE_TTL = timedelta(minutes=30)


class InvalidInviteRecipient(ValueError):
    """An explicit valid email address is required, without echoing it in errors."""


class InviteIssueUnavailable(ValueError):
    """Preparation expired or conflicted; whole-rollback before retrying."""


@dataclass(frozen=True)
class PreparedClientInvite:
    claim_id: int = field(repr=False)
    raw_token: str = field(repr=False)
    expires_at: datetime = field(repr=False)


def _require_mutation_access(db: Session, issuer: User) -> None:
    # Only refresh the payer's subscriptions. expire_all would discard pending proof state.
    payer_id = subscription_payer_id(db, issuer)
    if payer_id is not None:
        db.query(Subscription).filter(Subscription.user_id == payer_id).populate_existing().all()
    subscription_access.enforce_subscription_mutation_access(db, issuer.id)


def issue_client_invite(db: Session, issuer_user_id: int, client_id: int,
                        recipient_email: str) -> PreparedClientInvite:
    """Flush an invitation, never an account, grant, email or internal commit.

    issuer_user_id is selected by the authenticated caller; an id is not a credential.
    Future issue HTTP must revalidate the same CRM credential/revocation after waits.
    Internal raw_token is private: expose it only after caller commit succeeds. Any
    exception requires whole rollback, including unchanged billing HTTPException402,
    authorization Denied/Busy and unique proof conflicts. Accept has a separate core.
    """
    try:
        recipient = canonical_visitor_email(str(TypeAdapter(EmailStr).validate_python(recipient_email)))
    except (ValueError, TypeError):
        raise InvalidInviteRecipient('A valid invitation email is required') from None
    if any(isinstance(value, bool) or not isinstance(value, int) or value <= 0
           for value in (issuer_user_id, client_id)):
        raise visitor_invite_authorization.InviteAuthorizationDenied('Invitation authority is not available')
    with db.no_autoflush:
        # Staff issue takes User before Client. Visitor accept's reverse User leg is NOWAIT.
        issuer = db.query(User).filter(User.id == issuer_user_id, User.deleted_at.is_(None)
            ).populate_existing().with_for_update().one_or_none()
        if issuer is None:
            raise visitor_invite_authorization.InviteAuthorizationDenied('Invitation authority is not available')
        business_id = permissions.get_user_business_id(issuer, db)
        if business_id is None:
            raise visitor_invite_authorization.InviteAuthorizationDenied('Invitation authority is not available')
        client = db.query(Client.id).filter(Client.id == client_id, Client.deleted_at.is_(None)
            ).with_for_update(key_share=True).scalar()
        if client is None:
            raise visitor_invite_authorization.InviteAuthorizationDenied('Invitation authority is not available')
        visitor_invite_authorization.authorize_client_invite(db, issuer_user_id=issuer_user_id,
                                                            business_id=business_id, client_id=client_id)
        _require_mutation_access(db, issuer)
        raw_token = 'vci_' + secrets.token_urlsafe(32)
        now = utcnow()
        proof = VisitorClaim(kind='client_invite', proof_hash=hashlib.sha256(raw_token.encode('ascii')).hexdigest(),
            business_id=business_id, client_id=client_id, recipient_email=recipient, issued_by_user_id=issuer_user_id,
            created_at=now, expires_at=now + INVITE_TTL)
        db.add(proof)
        try:
            db.flush([proof])
        except IntegrityError as exc:
            if getattr(getattr(exc.orig, 'diag', None), 'constraint_name', None) == 'uq_visitor_claims_proof_hash':
                raise InviteIssueUnavailable('Invitation preparation is unavailable; retry after rollback') from None
            raise
        if proof.expires_at <= utcnow():
            raise InviteIssueUnavailable('Invitation preparation expired; retry after rollback')
        visitor_invite_authorization.authorize_client_invite(db, issuer_user_id=issuer_user_id,
                                                            business_id=business_id, client_id=client_id)
        _require_mutation_access(db, issuer)
        if proof.expires_at <= utcnow():
            raise InviteIssueUnavailable('Invitation preparation expired; retry after rollback')
        return PreparedClientInvite(proof.id, raw_token, proof.expires_at)
