"""The privacy policy quotes real retention numbers, so it must move when the code does."""
from datetime import timedelta
from pathlib import Path

import pytest

from app.api.core import visitor_challenges, visitor_client_invite_issue, visitor_recent_auth, visitor_sessions
from app.api.core.config import settings
from app.db.base import Base
import app.db.models  # noqa: F401  registers every table on Base.metadata
from app.db.models.visitor_access import VisitorClaim, VisitorGrant
from app.db.models.visitor_account import VisitorAccount, VisitorGoogleIdentity
from app.db.models.visitor_auth import VisitorChallenge, VisitorSession
from app.db.models.visitor_proof_mail_job import VisitorProofMailJob

DOC = Path(__file__).resolve().parents[1] / "docs" / "privacy-policy.md"


def _sections() -> tuple[str, str]:
    text = DOC.read_text(encoding="utf-8")
    english, czech = text.split("\n## Česky\n", 1)
    return english, czech


def _minutes(value: timedelta) -> int:
    return int(value.total_seconds() // 60)


def test_policy_has_english_and_czech_versions():
    english, czech = _sections()
    assert "## English" in english
    for heading in ("Who is responsible", "What we store", "How long", "Deleting your account", "Contact"):
        assert heading in english
    for heading in ("Kdo odpovídá", "Co ukládáme", "Jak dlouho", "Smazání účtu", "Kontakt"):
        assert heading in czech


def test_policy_names_the_controller_and_contact_in_both_languages():
    for part in _sections():
        assert "Alexandr Krajuškin" in part
        assert "24334995" in part
        assert "alexkrayushkin@outlook.com" in part


@pytest.mark.parametrize("english,czech,value", [
    ("{} days", "{} dní", lambda: visitor_sessions.SESSION_TTL.days),
    ("{} minutes", "{} minut", lambda: _minutes(visitor_client_invite_issue.INVITE_TTL)),
    ("{} minutes", "{} minut", lambda: settings.lockout_duration_minutes),
    ("{} minutes", "{} minut", lambda: _minutes(visitor_recent_auth.RECENT_AUTH_TTL)),
    ("{} minutes", "{} minut", lambda: _minutes(visitor_challenges.EMAIL_PROOF_TTL)),
    ("{} minutes", "{} minut", lambda: _minutes(visitor_challenges.GOOGLE_PROOF_TTL)),
])
def test_policy_quotes_current_retention_numbers(english, czech, value):
    en, cs = _sections()
    assert english.format(value()) in en
    assert czech.format(value()) in cs


def test_policy_covers_every_visitor_table():
    # A new visitor table means a new kind of stored data: the policy has to say what it is.
    tables = {name for name in Base.metadata.tables if name.startswith("visitor_")}
    assert {model.__tablename__ for model in (VisitorAccount, VisitorGoogleIdentity, VisitorChallenge,
            VisitorSession, VisitorGrant, VisitorClaim, VisitorProofMailJob)} <= tables
    text = DOC.read_text(encoding="utf-8")
    missing = sorted(table for table in tables if f"`{table}`" not in text)
    assert not missing, f"privacy policy does not describe: {missing}"
