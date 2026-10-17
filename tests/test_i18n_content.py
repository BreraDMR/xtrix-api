from app.api.core.i18n import _
from tests.tenant_world import build_tenant


def test_success_messages():
    assert _("success_created", "en") == "Successfully created"
    assert _("success_created", "ru") == "Успешно создано"
    assert _("success_updated", "cs") == "Úspěšně aktualizováno"
    assert _("reminders_sent", "ru") == "Напоминания отправлены"


def test_responses_carry_codes_not_translated_labels(client, make_user, headers_for):
    # the web client translates codes itself; a label like "Запланирован" would break `status === 'planned'`
    a = build_tenant(client, make_user, headers_for, "Alpha", "+42070011")
    ru = {**a["headers"], "Accept-Language": "ru"}
    r = client.put(f"/businesses/{a['business']['id']}", json={"name": "Alpha Salon 2"}, headers=ru)
    assert r.status_code == 200 and r.json()["business_type"] == "salon"
    roles = {u["role"] for u in client.get("/users/", headers=ru).json()["items"]}
    assert roles == {"business_owner", "specialist"}
    assert [v["status"] for v in client.get("/visits/", headers=ru).json()["items"]] == ["planned"]
    r = client.post("/users/", json={"full_name": "New", "email": "new@example.com", "phone": "+420777123499",
                                     "password": "Str0ng!Pass-word", "role": "specialist"}, headers={"Accept-Language": "ru"})
    assert r.json()["role"] == "specialist"
    # error messages are still localized
    assert client.get("/visits/999999", headers=ru).json()["error"]["message"] != "Visit not found"
