"""Erase visitor identity in one transaction, leaving salon-owned records alone."""
from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from app.api.core import visitor_recent_auth
from app.db.models.visitor_access import VisitorClaim
from app.db.models.visitor_account import VisitorAccount
from app.db.models.visitor_auth import VisitorChallenge
from app.db.models.visitor_proof_mail_job import VisitorProofMailJob


def delete_account(db: Session, raw_token: str | None) -> None:
    """Authenticate under the account lock. The caller commits or rolls back."""
    actor = visitor_recent_auth.lock_recent_actor(db, raw_token)
    challenges = select(VisitorChallenge.id).where(VisitorChallenge.account_id == actor.account_id)
    # SET NULL alone would leave an encrypted proof in the delivery queue.
    db.execute(delete(VisitorProofMailJob).where(VisitorProofMailJob.challenge_id.in_(challenges)))
    # Keep the spent proof hash and consumed_at. Deleting the account/grants only
    # clears their FKs, so a used link can never be claimed by the next signup.
    db.execute(update(VisitorClaim).where(VisitorClaim.consumed_account_id == actor.account_id,
        VisitorClaim.recipient_email.is_not(None)).values(recipient_email='deleted@visitor.invalid'))
    visitor_recent_auth.require_recent_actor(db, raw_token, actor)
    # Bulk deletion lets the database apply CASCADE/SET NULL instead of ORM nulling.
    db.execute(delete(VisitorAccount).where(VisitorAccount.id == actor.account_id))
