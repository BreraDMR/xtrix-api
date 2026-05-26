import pytest
from datetime import datetime
from app.api.core.i18n import localize_response, localize_field, _
def test_localize_field_role():
    assert localize_field("admin", "role", "en") == "Administrator"
    assert localize_field("admin", "role", "ru") == "Администратор"
    assert localize_field("admin", "role", "cs") == "Správce"
    assert localize_field("specialist", "role", "en") == "Specialist"
    assert localize_field("specialist", "role", "ru") == "Специалист"
    assert localize_field("client", "role", "cs") == "Klient"
def test_localize_field_status():
    assert localize_field("planned", "status", "en") == "Planned"
    assert localize_field("planned", "status", "ru") == "Запланирован"
    assert localize_field("completed", "status", "cs") == "Dokončeno"
    assert localize_field("cancelled", "status", "en") == "Cancelled"
    assert localize_field("cancelled", "status", "ru") == "Отменен"
def test_localize_response_dict():
    data = {
        "id": 1,
        "role": "admin",
        "status": "planned",
        "name": "Test",
    }
    result_en = localize_response(data, "en")
    assert result_en["role"] == "Administrator"
    assert result_en["status"] == "Planned"
    assert result_en["name"] == "Test"  
    result_ru = localize_response(data, "ru")
    assert result_ru["role"] == "Администратор"
    assert result_ru["status"] == "Запланирован"
def test_localize_response_list():
    data = [
        {"id": 1, "role": "admin", "status": "planned"},
        {"id": 2, "role": "specialist", "status": "completed"},
    ]
    result = localize_response(data, "ru")
    assert result[0]["role"] == "Администратор"
    assert result[0]["status"] == "Запланирован"
    assert result[1]["role"] == "Специалист"
    assert result[1]["status"] == "Завершен"
def test_localize_response_nested():
    data = {
        "user": {"id": 1, "role": "admin"},
        "visit": {"id": 1, "status": "planned"},
    }
    result = localize_response(data, "cs")
    assert result["user"]["role"] == "Správce"
    assert result["visit"]["status"] == "Naplánováno"
def test_localize_subscription_status():
    assert localize_field("active", "subscription", "en") == "Active"
    assert localize_field("active", "subscription", "ru") == "Активна"
    assert localize_field("expired", "subscription", "cs") == "Vypršela"
def test_success_messages():
    assert _("success_created", "en") == "Successfully created"
    assert _("success_created", "ru") == "Успешно создано"
    assert _("success_updated", "cs") == "Úspěšně aktualizováno"
    assert _("reminders_sent", "ru") == "Напоминания отправлены"
