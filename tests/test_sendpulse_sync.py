"""SendPulse has one address book for the whole installation, so syncing would mix every
salon's clients together. The sync stays off unless someone turns it on on purpose."""
import pytest

from app.api.core import sendpulse_sync
from app.api.core.config import settings


class _Spy:
    def __init__(self):
        self.calls = []

    def create_or_update_contact(self, **kwargs):
        self.calls.append(kwargs)
        return True


@pytest.fixture
def spy(monkeypatch):
    spy = _Spy()
    monkeypatch.setattr(sendpulse_sync, "get_sendpulse_client", lambda: spy)
    return spy


def test_sync_is_off_by_default():
    assert settings.sendpulse_contact_sync_enabled is False


def test_disabled_sync_sends_nothing(client, auth_headers, db, spy):
    r = client.post("/clients/", json={"full_name": "Jana", "contact_email": "jana@example.com"},
                    headers=auth_headers)
    assert r.status_code == 200, r.text

    assert sendpulse_sync.sync_client_to_sendpulse(db, r.json()["id"]) is False
    assert sendpulse_sync.sync_all_clients_to_sendpulse()["disabled"] is True
    assert sendpulse_sync.sync_all_users_to_sendpulse()["disabled"] is True
    assert sendpulse_sync.sync_all_contacts_to_sendpulse()["total"]["synced"] == 0
    assert spy.calls == []


def test_enabled_sync_still_works_when_turned_on(client, auth_headers, db, spy, monkeypatch):
    monkeypatch.setattr(settings, "sendpulse_contact_sync_enabled", True)
    r = client.post("/clients/", json={"full_name": "Jana", "contact_email": "jana@example.com"},
                    headers=auth_headers)
    assert sendpulse_sync.sync_client_to_sendpulse(db, r.json()["id"]) is True
    assert spy.calls[0]["email"] == "jana@example.com"
