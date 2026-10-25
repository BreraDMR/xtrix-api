"""Visitor identities have their own schema and never reuse CRM account tables."""
from sqlalchemy import inspect


def test_visitor_schema_is_separate_and_requires_a_verified_contact(db):
    schema = inspect(db.get_bind())
    tables = set(schema.get_table_names())
    assert {'visitor_accounts', 'visitor_google_identities'} <= tables
    columns = {column['name']: column for column in schema.get_columns('visitor_accounts')}
    assert columns['email_verified_at']['nullable'] is False
    assert columns['hashed_password']['nullable'] is True
    assert not schema.get_foreign_keys('visitor_accounts')
    assert {fk['referred_table'] for fk in schema.get_foreign_keys('visitor_google_identities')} == {'visitor_accounts'}


def test_visitor_credentials_have_separate_expiry_and_revocation_fields(db):
    schema = inspect(db.get_bind())
    assert {'visitor_challenges', 'visitor_sessions'} <= set(schema.get_table_names())
    challenge = {column['name'] for column in schema.get_columns('visitor_challenges')}
    session = {column['name'] for column in schema.get_columns('visitor_sessions')}
    assert {'purpose', 'token_hash', 'expires_at', 'consumed_at'} <= challenge
    assert {'account_id', 'token_hash', 'expires_at', 'revoked_at'} <= session
    assert {'raw_token', 'password', 'credential'}.isdisjoint(challenge | session)
