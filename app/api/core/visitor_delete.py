"""Erase visitor identity in one transaction, leaving salon-owned records alone."""
from sqlalchemy import and_, delete, or_, select, update
from sqlalchemy.orm import Session

from app.api.core import notification_push, visitor_recent_auth
from app.api.core.clock import utcnow
from app.db.models.visitor_access import VisitorClaim
from app.db.models.visitor_account import VisitorAccount
from app.db.models.visitor_auth import VisitorChallenge, VisitorSession
from app.db.models.visitor_proof_mail_job import VisitorProofMailJob


def delete_account(db: Session, raw_token: str | None) -> None:
    """Authenticate under the account lock. The caller commits or rolls back."""
    actor = visitor_recent_auth.lock_recent_actor(db, raw_token)
    session = db.get(VisitorSession, actor.session_id)
    deadline = min(session.expires_at, session.created_at + visitor_recent_auth.RECENT_AUTH_TTL)
    email = db.get(VisitorAccount, actor.account_id).email_canonical
    owned_proofs = or_(VisitorChallenge.account_id == actor.account_id,
        and_(VisitorChallenge.purpose == 'register', VisitorChallenge.email_canonical == email))
    # Freeze the exact set for both deletes. A later anonymous registration
    # request is a new proof, not an orphaned mail job from this deletion.
    challenge_ids = list(db.scalars(select(VisitorChallenge.id).where(owned_proofs).with_for_update()))
    # SET NULL alone would leave an encrypted proof in the delivery queue.
    db.execute(delete(VisitorProofMailJob).where(VisitorProofMailJob.challenge_id.in_(challenge_ids)))
    # Registration proofs are unbound, so the account cascade cannot erase them.
    # Remove them before deleting the account (a parallel signup still sees the
    # old unique email and cannot insert a replacement while holding a proof lock).
    db.execute(delete(VisitorChallenge).where(VisitorChallenge.id.in_(challenge_ids)))
    # Keep the spent proof hash and consumed_at. Deleting the account/grants only
    # clears their FKs, so a used link can never be claimed by the next signup.
    db.execute(update(VisitorClaim).where(VisitorClaim.consumed_account_id == actor.account_id,
        VisitorClaim.recipient_email.is_not(None)).values(recipient_email='deleted@visitor.invalid'))
    # The device rows go with the sessions; queued reminders would still name the phone.
    notification_push.erase_account_devices(db, actor.account_id)
    visitor_recent_auth.require_recent_actor(db, raw_token, actor)
    # Bulk deletion lets the database apply CASCADE/SET NULL instead of ORM nulling.
    db.execute(delete(VisitorAccount).where(VisitorAccount.id == actor.account_id))
    # CASCADE can wait on another transaction, after the session itself is gone.
    if utcnow() >= deadline:
        raise visitor_recent_auth.VisitorReauthenticationRequired('A recent visitor login is required')
