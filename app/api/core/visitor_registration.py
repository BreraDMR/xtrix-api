"""Create a new visitor only after registration proof; callers own commit/rollback."""
from dataclasses import dataclass, field

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.core.clock import utcnow
from app.api.core.security import validate_password_strength
from app.api.core import visitor_challenges, visitor_passwords, visitor_sessions
from app.db.models.visitor_account import VisitorAccount
from app.db.models.visitor_auth import VisitorSession


class InvalidRegistrationProof(ValueError):
    """Unknown, expired, consumed or purpose-mismatched registration proof."""


class InvalidRegistrationPassword(ValueError):
    """The proposed password does not meet the existing creation policy."""


class RegistrationConflict(ValueError):
    """Another verified visitor already owns this canonical email."""


@dataclass(frozen=True)
class CompletedVisitorRegistration:
    account: VisitorAccount
    session: VisitorSession
    raw_session_token: str = field(repr=False)


def complete_registration(db: Session, raw_proof: str | None,
                          new_password: str) -> CompletedVisitorRegistration:
    """Flush proof + NEW account + session together. Never merge an existing account.

    Every exception requires the caller to roll back before reusing the session.
    We lock the unbound Challenge first. The only Account later locked by session
    creation is our own new uncommitted row, never another account's row.
    """
    proof = visitor_challenges.consume_challenge(db, raw_proof, purpose="register")
    if proof is None:
        raise InvalidRegistrationProof("Registration proof is not available")
    if not isinstance(new_password, str) or not validate_password_strength(new_password)[0]:
        raise InvalidRegistrationPassword("Password does not meet the creation policy")
    try:
        encoded = visitor_passwords.hash_password(new_password)
    except ValueError:
        raise InvalidRegistrationPassword("Password does not meet the creation policy") from None
    account = VisitorAccount(email_canonical=proof.email_canonical,
                             email_verified_at=utcnow(), hashed_password=encoded)
    db.add(account)
    try:
        db.flush([account])
    except IntegrityError as exc:
        if getattr(getattr(exc.orig, "diag", None), "constraint_name", None) == "uq_visitor_accounts_email_canonical":
            raise RegistrationConflict("Registration is not available") from None
        raise
    # A conflicting uncommitted email can keep the unique insert waiting past expiry.
    if proof.expires_at <= utcnow():
        raise InvalidRegistrationProof("Registration proof is not available")
    session, raw_session = visitor_sessions.create_session(db, account)
    return CompletedVisitorRegistration(account, session, raw_session)
