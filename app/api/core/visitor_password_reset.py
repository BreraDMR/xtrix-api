"""Reset a visitor password and revoke sessions in the caller's transaction."""
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.api.core import visitor_challenges, visitor_passwords, visitor_sessions
from app.api.core.clock import utcnow
from app.api.core.security import validate_password_strength
from app.db.models.visitor_account import VisitorAccount
from app.db.models.visitor_auth import VisitorChallenge


class InvalidResetProof(ValueError):
    """The proof is unavailable, expired, replayed or no longer bound to this email."""


class InvalidResetPassword(ValueError):
    """The proposed password does not meet the existing creation policy."""


@dataclass(frozen=True)
class CompletedPasswordReset:
    account_id: int = field(repr=False)
    revoked_sessions: int


def confirm_reset(db: Session, raw_proof: str | None, new_password: str) -> CompletedPasswordReset:
    """Flush password + proof + revocation atomically. Caller commits or whole rolls back.

    consume_challenge locks the current Account before its reset Challenge and checks
    the saved email binding. No session is issued, including for Google-only accounts.
    """
    proof = visitor_challenges.consume_challenge(db, raw_proof, purpose="reset")
    if proof is None:
        raise InvalidResetProof("Password reset proof is not available")
    if not isinstance(new_password, str) or not validate_password_strength(new_password)[0]:
        raise InvalidResetPassword("Password does not meet the creation policy")
    account = db.get(VisitorAccount, proof.account_id)
    if account is None:
        raise InvalidResetProof("Password reset proof is not available")
    try:
        encoded = visitor_passwords.hash_password(new_password)
    except ValueError:
        raise InvalidResetPassword("Password does not meet the creation policy") from None
    account.hashed_password = encoded
    # This helper reuses the Account lock without refreshing away the pending password.
    revoked = visitor_sessions.revoke_all_sessions(db, account_id=account.id)
    db.query(VisitorChallenge).filter(
        VisitorChallenge.account_id == account.id, VisitorChallenge.purpose == "reset",
        VisitorChallenge.consumed_at.is_(None),
    ).update({"consumed_at": utcnow()}, synchronize_session="fetch")
    db.flush()
    if proof.expires_at <= utcnow():
        raise InvalidResetProof("Password reset proof is not available")
    return CompletedPasswordReset(account.id, revoked)
