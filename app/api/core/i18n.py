from typing import Dict, Any, Optional

SUPPORTED_LOCALES = ("en", "ru", "cz")

messages: Dict[str, Dict[str, str]] = {
    "phone_exists": {
        "en": "Phone already registered",
        "ru": "Телефон уже зарегистрирован",
        "cz": "Telefon je již registrován",
    },
    "email_exists": {
        "en": "Email already registered",
        "ru": "Email уже зарегистрирован",
        "cz": "Email je již registrován",
    },
    "invalid_credentials": {
        "en": "Invalid credentials",
        "ru": "Неверные учетные данные",
        "cz": "Neplatné přihlašovací údaje",
    },
    "not_allowed": {
        "en": "Not enough permissions",
        "ru": "Недостаточно прав",
        "cz": "Nedostatečná oprávnění",
    },
    "client_not_found": {
        "en": "Client not found",
        "ru": "Клиент не найден",
        "cz": "Klient nebyl nalezen",
    },
    "visit_not_found": {
        "en": "Visit not found",
        "ru": "Визит не найден",
        "cz": "Návštěva nebyla nalezena",
    },
    "service_not_found": {
        "en": "Service not found",
        "ru": "Услуга не найдена",
        "cz": "Služba nebyla nalezena",
    },
    "plan_not_found": {
        "en": "Plan not found",
        "ru": "План не найден",
        "cz": "Plán nebyl nalezen",
    },
    "plan_not_active": {
        "en": "Plan not active",
        "ru": "План не активен",
        "cz": "Plán není aktivní",
    },
    "s3_not_configured": {
        "en": "S3 bucket not configured",
        "ru": "S3 бакет не настроен",
        "cz": "S3 bucket není nastaven",
    },
    "no_slot": {
        "en": "No free slot in next 7 days",
        "ru": "Нет свободного слота в ближайшие 7 дней",
        "cz": "Žádný volný termín v následujících 7 dnech",
    },
    "cannot_create_for_owner": {
        "en": "Cannot create for another owner",
        "ru": "Нельзя создавать для другого владельца",
        "cz": "Nelze vytvořit pro jiného vlastníka",
    },
    # Content translations
    "role_admin": {
        "en": "Administrator",
        "ru": "Администратор",
        "cz": "Správce",
    },
    "role_specialist": {
        "en": "Specialist",
        "ru": "Специалист",
        "cz": "Specialista",
    },
    "role_client": {
        "en": "Client",
        "ru": "Клиент",
        "cz": "Klient",
    },
    "status_planned": {
        "en": "Planned",
        "ru": "Запланирован",
        "cz": "Naplánováno",
    },
    "status_completed": {
        "en": "Completed",
        "ru": "Завершен",
        "cz": "Dokončeno",
    },
    "status_cancelled": {
        "en": "Cancelled",
        "ru": "Отменен",
        "cz": "Zrušeno",
    },
    "status_in_progress": {
        "en": "In Progress",
        "ru": "В процессе",
        "cz": "V průběhu",
    },
    "subscription_active": {
        "en": "Active",
        "ru": "Активна",
        "cz": "Aktivní",
    },
    "subscription_expired": {
        "en": "Expired",
        "ru": "Истекла",
        "cz": "Vypršela",
    },
    "subscription_cancelled": {
        "en": "Cancelled",
        "ru": "Отменена",
        "cz": "Zrušena",
    },
    "success_created": {
        "en": "Successfully created",
        "ru": "Успешно создано",
        "cz": "Úspěšně vytvořeno",
    },
    "success_updated": {
        "en": "Successfully updated",
        "ru": "Успешно обновлено",
        "cz": "Úspěšně aktualizováno",
    },
    "success_deleted": {
        "en": "Successfully deleted",
        "ru": "Успешно удалено",
        "cz": "Úspěšně smazáno",
    },
    "reminders_sent": {
        "en": "Reminders sent",
        "ru": "Напоминания отправлены",
        "cz": "Připomínky odeslány",
    },
    "invalid_refresh_token": {
        "en": "Invalid refresh token",
        "ru": "Недействительный refresh token",
        "cz": "Neplatný refresh token",
    },
    "refresh_token_not_found": {
        "en": "Refresh token not found",
        "ru": "Refresh token не найден",
        "cz": "Refresh token nebyl nalezen",
    },
    "user_not_found": {
        "en": "User not found",
        "ru": "Пользователь не найден",
        "cz": "Uživatel nebyl nalezen",
    },
    "forbidden": {
        "en": "Forbidden",
        "ru": "Запрещено",
        "cz": "Zakázáno",
    },
    "logout_success": {
        "en": "Successfully logged out",
        "ru": "Успешный выход",
        "cz": "Úspěšně odhlášeno",
    },
    "logout_all_success": {
        "en": "Successfully logged out from all devices",
        "ru": "Успешный выход со всех устройств",
        "cz": "Úspěšně odhlášeno ze všech zařízení",
    },
    "account_unlocked": {
        "en": "Account unlocked successfully",
        "ru": "Аккаунт успешно разблокирован",
        "cz": "Účet byl úspěšně odemčen",
    },
    "account_locked": {
        "en": "Account is locked due to too many failed login attempts",
        "ru": "Аккаунт заблокирован из-за слишком многих неудачных попыток входа",
        "cz": "Účet je zablokován kvůli příliš mnoha neúspěšným pokusům o přihlášení",
    },
    "password_weak": {
        "en": "Password does not meet security requirements",
        "ru": "Пароль не соответствует требованиям безопасности",
        "cz": "Heslo nesplňuje bezpečnostní požadavky",
    },
    "invalid_phone_format": {
        "en": "Invalid phone number format. Use international format (e.g., +1234567890)",
        "ru": "Неверный формат номера телефона. Используйте международный формат (например, +1234567890)",
        "cz": "Neplatný formát telefonního čísla. Použijte mezinárodní formát (např. +1234567890)",
    },
    "invalid_qr_code": {
        "en": "Invalid QR code",
        "ru": "Неверный QR-код",
        "cz": "Neplatný QR kód",
    },
    "invalid_password": {
        "en": "Invalid password",
        "ru": "Неверный пароль",
        "cz": "Neplatné heslo",
    },
    "invalid_report_type": {
        "en": "Invalid report type",
        "ru": "Неверный тип отчета",
        "cz": "Neplatný typ zprávy",
    },
}


def normalize_locale(locale: str | None) -> str:
    if not locale:
        return "en"
    locale = locale.split(",")[0].split("-")[0].lower()
    return locale if locale in SUPPORTED_LOCALES else "en"


def _(key: str, locale: str | None = None) -> str:
    loc = normalize_locale(locale)
    return messages.get(key, {}).get(loc, messages.get(key, {}).get("en", key))


def localize_field(value: str, field_type: str, locale: str | None = None) -> str:
    """Localize field values like status, role, etc."""
    if not value:
        return value
    key = f"{field_type}_{value.lower()}"
    translated = _(key, locale)
    return translated if translated != key else value


def localize_response(data: Any, locale: str | None = None) -> Any:
    """Recursively localize response data."""
    if isinstance(data, dict):
        result = {}
        for key, value in data.items():
            if key == "role" and isinstance(value, str):
                result[key] = localize_field(value, "role", locale)
            elif key == "status" and isinstance(value, str):
                result[key] = localize_field(value, "status", locale)
            elif key == "subscription_status" and isinstance(value, str):
                result[key] = localize_field(value, "subscription", locale)
            elif isinstance(value, (dict, list)):
                result[key] = localize_response(value, locale)
            else:
                result[key] = value
        return result
    elif isinstance(data, list):
        return [localize_response(item, locale) for item in data]
    return data

