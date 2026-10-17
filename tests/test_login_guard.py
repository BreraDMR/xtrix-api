"""Wrong passwords can't lock the owner out any more - they only stop the address they come from."""
import pytest

from app.api.core import config

PASSWORD = "Str0ng!Pass-word"


@pytest.fixture
def from_ip(client, monkeypatch):
    # behind our proxy the last X-Forwarded-For hop is the client - lets one test client be many IPs
    monkeypatch.setattr(config.settings, "trust_proxy", True)

    def _login(ip, email, password):
        return client.post("/users/login", json={"email": email, "password": password},
                           headers={"X-Forwarded-For": ip})

    return _login


def test_attacker_ip_is_stopped_but_owner_still_logs_in(make_user, from_ip):
    victim = make_user(role="business_owner")
    codes = [from_ip("203.0.113.66", victim.email, "Wr0ng!guess-1").status_code for _ in range(6)]
    assert codes == [401] * 5 + [429]

    blocked = from_ip("203.0.113.66", victim.email, PASSWORD)
    # the attacker's address can't even confirm a right guess
    assert blocked.status_code == 429
    assert blocked.json()["error"]["status_code"] == 429
    assert int(blocked.headers["retry-after"]) > 0

    owner = from_ip("198.51.100.7", victim.email, PASSWORD)
    assert owner.status_code == 200, owner.text
    assert owner.json()["access_token"]


def test_spread_out_guessing_hits_the_account_cap_only_for_dirty_ips(make_user, from_ip, monkeypatch):
    monkeypatch.setattr(config.settings, "login_account_failure_limit", "3/hour")
    victim = make_user(role="business_owner")
    for n in range(3):
        assert from_ip(f"203.0.113.{n}", victim.email, "Wr0ng!guess-1").status_code == 401

    # past the cap, an address that already failed on this account is refused...
    assert from_ip("203.0.113.0", victim.email, PASSWORD).status_code == 429
    # ...a clean one still gets in
    assert from_ip("198.51.100.8", victim.email, PASSWORD).status_code == 200


def test_success_clears_the_count_for_that_address(make_user, from_ip):
    user = make_user(role="specialist")
    for _ in range(4):
        assert from_ip("192.0.2.10", user.email, "Wr0ng!guess-1").status_code == 401
    assert from_ip("192.0.2.10", user.email, PASSWORD).status_code == 200
    # a fresh window: four more typos don't hit the old count
    for _ in range(4):
        assert from_ip("192.0.2.10", user.email, "Wr0ng!guess-1").status_code == 401
    assert from_ip("192.0.2.10", user.email, PASSWORD).status_code == 200


def test_wrong_passwords_no_longer_lock_the_account_row(make_user, from_ip, db):
    from app.db.models.user import User

    user = make_user(role="specialist")
    for _ in range(6):
        from_ip("203.0.113.99", user.email, "Wr0ng!guess-1")
    db.expire_all()
    row = db.query(User).filter(User.id == user.id).one()
    assert row.is_locked is False
