from datetime import datetime
from types import SimpleNamespace

import pytest

from app.api.core import notify
from app.api.core.client_messages import render
from tests.tenant_world import build_tenant


@pytest.mark.parametrize("lang,word", [("en", "Happy birthday"), ("ru", "днём рождения"), ("cz", "narozeninám")])
def test_birthday_text_per_language(lang, word):
    msg = render("birthday", lang, name="Jana", business_name="Alpha Salon")
    assert word.lower() in (msg.subject + msg.body).lower()
    assert "Alpha Salon" in msg.body and "Jana" in msg.body


def test_unknown_language_falls_back_to_russian():
    assert render("followup", "de", name="X").subject == render("followup", "ru", name="X").subject


def test_language_is_a_client_setting(client, auth_headers):
    r = client.post("/clients/", json={"full_name": "Jana", "contact_phone": "+420777000555"}, headers=auth_headers)
    assert r.status_code == 200 and r.json()["language"] == "ru"
    cid = r.json()["id"]
    assert client.put(f"/clients/{cid}", json={"language": "cz"}, headers=auth_headers).json()["language"] == "cz"
    assert client.put(f"/clients/{cid}", json={"language": "de"}, headers=auth_headers).status_code == 422
    r = client.post("/clients/", json={"full_name": "John", "contact_phone": "+420777000556", "language": "en"},
                    headers=auth_headers)
    assert r.json()["language"] == "en"


def test_reminder_goes_out_in_the_clients_language(monkeypatch):
    sent = []
    monkeypatch.setattr(notify, "_send_email", lambda to, subject, body: sent.append((to, subject, body)) or True)
    visit = SimpleNamespace(id=1, client_id=7, date_start=datetime(2030, 1, 2, 10, 0), service_name="Masáž")
    contacts = {7: {"email": "jana@example.com", "language": "cz", "name": "Jana"}}

    assert notify.send_notifications([visit], channel="email", contacts=contacts) == 1
    assert sent[0][1] == "Připomínka návštěvy"
    assert "02.01.2030 10:00" in sent[0][2] and "Masáž" in sent[0][2]


def test_birthday_job_uses_each_clients_language(client, make_user, headers_for, monkeypatch):
    from app.api.core import notification_tasks

    a = build_tenant(client, make_user, headers_for, "Alpha", "+42070011")
    today = datetime.utcnow().date()
    r = client.put(f"/clients/{a['client']['id']}", headers=a["headers"],
                   json={"language": "cz", "birth_date": today.replace(year=1992).isoformat()})
    assert r.status_code == 200, r.text
    sent = []
    monkeypatch.setattr(notify, "_send_email", lambda to, subject, body: sent.append(subject) or True)

    assert notification_tasks.send_birthday_greetings(channel="email") == 1
    assert sent == ["🎉 Všechno nejlepší k narozeninám!"]
