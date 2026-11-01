"""New CRM secrets are complete; old bcrypt records keep their original meaning."""
import bcrypt
import pytest

from app.api.core import crm_passwords, security, visitor_passwords
from app.api.crud import crud_password_reset
from app.db.models.user import User


@pytest.mark.parametrize('prefix', ['Aa1!Zp9@' * 9, 'Aa1!' + 'Жя' * 17])
def test_new_hash_distinguishes_suffix_after_72_utf8_bytes(prefix):
    assert len(prefix.encode()) == 72
    encoded = security.get_password_hash(prefix + 'A')
    assert security.verify_password(prefix + 'A', encoded)
    assert not security.verify_password(prefix + 'B', encoded)
    assert not security.verify_password(prefix, encoded)


@pytest.mark.parametrize('raw,other', [
    ('🙂' * 127 + 'A', '🙂' * 127 + 'B'),
    ('  Full!Secret-29  ', 'Full!Secret-29'),
    ('e\u0301-secret', 'é-secret'),
    ('before\0afterA', 'before\0afterB'),
])
def test_full_unicode_whitespace_and_null_bytes_are_preserved(raw, other):
    encoded = security.get_password_hash(raw)
    assert security.verify_password(raw, encoded)
    assert not security.verify_password(other, encoded)


def test_versioned_salts_are_unique_and_domains_cannot_be_relabeled():
    raw = 'Synthetic!CRM-Password-29'
    first, second = security.get_password_hash(raw), security.get_password_hash(raw)
    assert first != second
    fields = first.split('$')
    assert fields[:6] == ['', 'xtrix-crm-scrypt', 'v1', '32768', '8', '3']
    assert len(bytes.fromhex(fields[6])) == 16
    assert len(bytes.fromhex(fields[7])) == 32
    assert raw not in first
    assert not visitor_passwords.verify_password(raw, first)
    visitor = visitor_passwords.hash_password(raw)
    assert not security.verify_password(raw, visitor)
    assert not security.verify_password(raw, visitor.replace('xtrix-visitor-scrypt', 'xtrix-crm-scrypt'))
    assert not visitor_passwords.verify_password(raw, first.replace('xtrix-crm-scrypt', 'xtrix-visitor-scrypt'))


@pytest.mark.parametrize('invalid', [None, True, 42, b'bytes', '', 'x' * 129, '\ud800'])
def test_invalid_new_password_never_reaches_kdf(monkeypatch, invalid):
    def forbidden(*_args):
        pytest.fail('Invalid password reached KDF')
    monkeypatch.setattr(crm_passwords, '_derive', forbidden)
    assert not crm_passwords.verify_password(invalid, None)
    with pytest.raises(ValueError):
        security.get_password_hash(invalid)


@pytest.mark.parametrize('stored', [None, True, '', 'not-a-hash', '$2b$malformed',
    '$xtrix-crm-scrypt$v2$32768$8$3$' + 'a' * 32 + '$' + 'b' * 64,
    '$xtrix-crm-scrypt$v1$999999999999$8$3$' + 'a' * 32 + '$' + 'b' * 64,
    '$xtrix-crm-scrypt$v1$32768$8$3$' + 'A' * 32 + '$' + 'b' * 64,
    '$xtrix-crm-scrypt$v1$32768$8$3$' + 'a' * 32 + '$' + 'b' * 64 + '\n',
    'x' * 1000000])
def test_malformed_hash_and_unbounded_parameters_fail_closed(monkeypatch, stored):
    def forbidden(*_args):
        pytest.fail('Malformed hash reached KDF')
    monkeypatch.setattr(crm_passwords, '_derive', forbidden)
    assert not security.verify_password('Candidate!Password-29', stored)


def test_missing_kdf_has_no_bcrypt_or_fast_hash_fallback(monkeypatch):
    encoded = security.get_password_hash('Candidate!Password-29')
    monkeypatch.setattr(visitor_passwords.hashlib, 'scrypt', None)
    for operation in [lambda: security.get_password_hash('Candidate!Password-29'),
                      lambda: security.verify_password('Candidate!Password-29', encoded)]:
        with pytest.raises(RuntimeError, match='CRM password hashing is unavailable'):
            operation()


@pytest.mark.parametrize('prefix', ['Aa1!Zp9@' * 9, 'Aa1!' + 'Жя' * 17])
def test_legacy_bcrypt_keeps_exact_truncation_without_widening_or_reencoding(prefix):
    password = prefix + 'A'
    raw = security._truncate_password_bytes(password.encode())
    encoded = bcrypt.hashpw(raw, bcrypt.gensalt()).decode()
    assert security.verify_password(password, encoded)
    # Old records cannot prove either suffix. Only an explicit reset establishes a full secret.
    assert security.verify_password(prefix + 'B', encoded)
    assert not security.verify_password('wrong-' + password, encoded)


LONG_PASSWORD = 'Aa1!' + 'Жя' * 35 + 'A'
OTHER_PASSWORD = LONG_PASSWORD[:-1] + 'B'


def test_crm_registration_login_and_reset_preserve_complete_password(client, db, monkeypatch):
    from app.api.core.config import settings
    monkeypatch.setattr(settings, 'pilot_mode_enabled', False)
    assert len(LONG_PASSWORD) <= 128 < len(LONG_PASSWORD.encode())
    body = {'full_name': 'Synthetic CRM Owner', 'email': 'crm.long@example.com',
            'role': 'business_owner', 'password': LONG_PASSWORD}
    created = client.post('/users/', json=body)
    assert created.status_code == 200, created.text
    owner = db.get(User, created.json()['id'])
    assert owner.hashed_password.startswith('$xtrix-crm-scrypt$v1$')
    login = {'email': body['email'], 'password': OTHER_PASSWORD}
    assert client.post('/users/login', json=login).status_code == 401
    login['password'] = LONG_PASSWORD
    assert client.post('/users/login', json=login).status_code == 200
    token, raw = crud_password_reset.issue_token(db, owner.id)
    db.commit()
    reset = client.post('/auth/password-reset/confirm', json={'token': raw, 'new_password': OTHER_PASSWORD})
    assert reset.status_code == 200, reset.text
    assert client.post('/users/login', json=login).status_code == 401
    login['password'] = OTHER_PASSWORD
    assert client.post('/users/login', json=login).status_code == 200


def test_legacy_login_does_not_rehash_an_unverified_suffix(client, db, make_user):
    prefix = 'Aa1!Zp9@' * 9
    owner = make_user(email='legacy.long@example.com')
    encoded = bcrypt.hashpw(prefix.encode(), bcrypt.gensalt()).decode()
    owner.hashed_password = encoded
    db.commit()
    response = client.post('/users/login', json={'email': owner.email, 'password': prefix + 'UnverifiedSuffix'})
    assert response.status_code == 200, response.text
    db.expire_all()
    assert db.get(User, owner.id).hashed_password == encoded


def test_google_link_checks_the_complete_new_crm_password(client, db, make_user, monkeypatch):
    from app.api.core.config import settings
    from app.db.models.google_identity import GoogleIdentity
    from tests.test_google_login import ORIGIN, sign_in
    monkeypatch.setattr(settings, 'google_client_id', 'pilot.apps.googleusercontent.com')
    monkeypatch.setattr(settings, 'frontend_url', ORIGIN)
    monkeypatch.setattr(settings, 'pilot_mode_enabled', True)
    monkeypatch.setattr(settings, 'pilot_invited_emails', 'invited@example.com')
    owner = make_user(email='invited@example.com', password=LONG_PASSWORD)
    wrong = sign_in(client, monkeypatch, link_existing=True, password=OTHER_PASSWORD)
    assert wrong.status_code == 401, wrong.text
    assert db.query(GoogleIdentity).count() == 0
    correct = sign_in(client, monkeypatch, link_existing=True, password=LONG_PASSWORD)
    assert correct.status_code == 200, correct.text
    assert db.query(GoogleIdentity).one().user_id == owner.id
