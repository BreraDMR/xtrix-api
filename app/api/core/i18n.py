from typing import Dict, Any
SUPPORTED_LOCALES = ("en", "ru", "cz")
messages: Dict[str, Dict[str, str]] = {
    "2fa_disabled": {
        "en": "Two-factor authentication disabled",
        "ru": "Двухфакторная аутентификация отключена",
        "cz": "Dvoufázové ověření vypnuto",
    },
    "2fa_enabled": {
        "en": "Two-factor authentication enabled",
        "ru": "Двухфакторная аутентификация включена",
        "cz": "Dvoufázové ověření zapnuto",
    },
    "2fa_not_enabled": {
        "en": "Two-factor authentication is not enabled",
        "ru": "Двухфакторная аутентификация не включена",
        "cz": "Dvoufázové ověření není zapnuto",
    },
    "2fa_not_setup": {
        "en": "Set up two-factor authentication first",
        "ru": "Сначала настройте двухфакторную аутентификацию",
        "cz": "Nejprve nastavte dvoufázové ověření",
    },
    "adjustment_deleted": {
        "en": "Adjustment deleted",
        "ru": "Корректировка удалена",
        "cz": "Úprava smazána",
    },
    "adjustment_not_found": {
        "en": "Adjustment not found",
        "ru": "Корректировка не найдена",
        "cz": "Úprava nenalezena",
    },
    "business_id_required_for_client_creation": {
        "en": "Create a business first",
        "ru": "Сначала создайте бизнес",
        "cz": "Nejprve vytvořte firmu",
    },
    "client_exists": {
        "en": "This client is already in your list",
        "ru": "Этот клиент уже есть в вашем списке",
        "cz": "Tento klient už ve vašem seznamu je",
    },
    "business_not_found": {
        "en": "Business not found",
        "ru": "Бизнес не найден",
        "cz": "Firma nenalezena",
    },
    "business_owner_id_required": {
        "en": "Business owner is required",
        "ru": "Нужно указать владельца бизнеса",
        "cz": "Je potřeba uvést majitele firmy",
    },
    "business_required": {
        "en": "This needs a business - create or join one first",
        "ru": "Нужен бизнес — сначала создайте его или присоединитесь",
        "cz": "Je potřeba firma - nejprve ji vytvořte nebo se připojte",
    },
    "cannot_cancel_subscription": {
        "en": "This subscription can't be cancelled",
        "ru": "Эту подписку нельзя отменить",
        "cz": "Toto předplatné nelze zrušit",
    },
    "cannot_reactivate_subscription": {
        "en": "This subscription can't be reactivated",
        "ru": "Эту подписку нельзя возобновить",
        "cz": "Toto předplatné nelze obnovit",
    },
    "email_or_phone_required": {
        "en": "Email or phone is required",
        "ru": "Нужен email или телефон",
        "cz": "Je potřeba e-mail nebo telefon",
    },
    "employee_already_fired": {
        "en": "The employee has already been dismissed",
        "ru": "Сотрудник уже уволен",
        "cz": "Zaměstnanec už byl propuštěn",
    },
    "employee_not_found": {
        "en": "Employee not found",
        "ru": "Сотрудник не найден",
        "cz": "Zaměstnanec nenalezen",
    },
    "invalid_2fa_token": {
        "en": "Invalid two-factor code",
        "ru": "Неверный код двухфакторной аутентификации",
        "cz": "Neplatný ověřovací kód",
    },
    "loyalty_level_deleted": {
        "en": "Loyalty level deleted",
        "ru": "Уровень лояльности удалён",
        "cz": "Věrnostní úroveň smazána",
    },
    "loyalty_level_not_found": {
        "en": "Loyalty level not found",
        "ru": "Уровень лояльности не найден",
        "cz": "Věrnostní úroveň nenalezena",
    },
    "loyalty_program_already_exists": {
        "en": "The business already has a loyalty programme",
        "ru": "У бизнеса уже есть программа лояльности",
        "cz": "Firma už má věrnostní program",
    },
    "loyalty_program_deleted": {
        "en": "Loyalty programme deleted",
        "ru": "Программа лояльности удалена",
        "cz": "Věrnostní program smazán",
    },
    "loyalty_program_not_found": {
        "en": "Loyalty programme not found",
        "ru": "Программа лояльности не найдена",
        "cz": "Věrnostní program nenalezen",
    },
    "loyalty_promotion_deleted": {
        "en": "Promotion deleted",
        "ru": "Акция удалена",
        "cz": "Akce smazána",
    },
    "loyalty_promotion_not_found": {
        "en": "Promotion not found",
        "ru": "Акция не найдена",
        "cz": "Akce nenalezena",
    },
    "no_unpaid_salary": {
        "en": "No unpaid salary",
        "ru": "Невыплаченной зарплаты нет",
        "cz": "Žádná nevyplacená mzda",
    },
    "payment_amount_exceeds_remaining": {
        "en": "The amount is more than what's left to pay",
        "ru": "Сумма больше остатка к выплате",
        "cz": "Částka převyšuje zbývající dlužnou částku",
    },
    "payment_amount_invalid": {
        "en": "Invalid payment amount",
        "ru": "Неверная сумма выплаты",
        "cz": "Neplatná částka platby",
    },
    "payment_deleted": {
        "en": "Payment deleted",
        "ru": "Выплата удалена",
        "cz": "Platba smazána",
    },
    "payment_method_required_for_completed_visits": {
        "en": "Payment method is required for a completed paid visit",
        "ru": "Для завершённого платного визита нужен способ оплаты",
        "cz": "U dokončené placené návštěvy je potřeba způsob platby",
    },
    "payment_not_found": {
        "en": "Payment not found",
        "ru": "Выплата не найдена",
        "cz": "Platba nenalezena",
    },
    "phone_or_email_required": {
        "en": "Phone or email is required",
        "ru": "Нужен телефон или email",
        "cz": "Je potřeba telefon nebo e-mail",
    },
    "points_used_successfully": {
        "en": "Points used",
        "ru": "Баллы списаны",
        "cz": "Body uplatněny",
    },
    "relation_deleted": {
        "en": "Link removed",
        "ru": "Связь удалена",
        "cz": "Vazba odstraněna",
    },
    "relation_not_found": {
        "en": "Link not found",
        "ru": "Связь не найдена",
        "cz": "Vazba nenalezena",
    },
    "salary_already_paid": {
        "en": "This salary is already paid",
        "ru": "Эта зарплата уже выплачена",
        "cz": "Tato mzda je už vyplacena",
    },
    "salary_calculation_not_found": {
        "en": "Salary calculation not found",
        "ru": "Расчёт зарплаты не найден",
        "cz": "Výpočet mzdy nenalezen",
    },
    "stripe_subscriptions_must_be_created_via_payment": {
        "en": "Stripe subscriptions are created through checkout",
        "ru": "Подписка Stripe оформляется через оплату",
        "cz": "Předplatné Stripe se zakládá přes platbu",
    },
    "subscription_not_found": {
        "en": "Subscription not found",
        "ru": "Подписка не найдена",
        "cz": "Předplatné nenalezeno",
    },
    "user_already_exists": {
        "en": "User already exists",
        "ru": "Пользователь уже существует",
        "cz": "Uživatel už existuje",
    },
    "invalid_phone_or_email": {
        "en": "Enter a valid phone number or email",
        "ru": "Укажите корректный телефон или email",
        "cz": "Zadejte platný telefon nebo e-mail",
    },
    "invalid_user_data": {
        "en": "Check the name, phone and email",
        "ru": "Проверьте имя, телефон и email",
        "cz": "Zkontrolujte jméno, telefon a e-mail",
    },
    "user_create_failed": {
        "en": "Could not create the user",
        "ru": "Не удалось создать пользователя",
        "cz": "Uživatele se nepodařilo vytvořit",
    },
    "user_already_has_business": {
        "en": "You already have a business",
        "ru": "У вас уже есть бизнес",
        "cz": "Už máte firmu",
    },
    "qr_access_locked": {
        "en": "Too many wrong codes, access is locked for 30 minutes",
        "ru": "Слишком много неверных кодов, доступ закрыт на 30 минут",
        "cz": "Příliš mnoho chybných kódů, přístup je na 30 minut zablokován",
    },
    "time_slot_taken": {
        "en": "This time is already booked for the specialist",
        "ru": "У специалиста уже есть запись на это время",
        "cz": "Specialista má na tento čas už rezervaci",
    },
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
    "product_not_found": {
        "en": "Product not found",
        "ru": "Товар не найден",
        "cz": "Produkt nebyl nalezen",
    },
    "product_deleted": {
        "en": "Product deleted successfully",
        "ru": "Товар успешно удален",
        "cz": "Produkt byl úspěšně smazán",
    },
    "expense_not_found": {
        "en": "Expense not found",
        "ru": "Расход не найден",
        "cz": "Výdaj nebyl nalezen",
    },
    "expense_deleted": {
        "en": "Expense deleted successfully",
        "ru": "Расход успешно удален",
        "cz": "Výdaj byl úspěšně smazán",
    },
    "category_not_found": {
        "en": "Category not found",
        "ru": "Категория не найдена",
        "cz": "Kategorie nebyla nalezena",
    },
    "category_deleted": {
        "en": "Category deleted successfully",
        "ru": "Категория успешно удалена",
        "cz": "Kategorie byla úspěšně smazána",
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
    "specialist_profile_not_found": {
        "en": "Specialist profile not found",
        "ru": "Профиль специалиста не найден",
        "cz": "Profil specialisty nebyl nalezen",
    },
    "profile_already_exists": {
        "en": "Profile already exists for this user",
        "ru": "Профиль уже существует для этого пользователя",
        "cz": "Profil již existuje pro tohoto uživatele",
    },
    "user_not_specialist": {
        "en": "User is not a specialist",
        "ru": "Пользователь не является специалистом",
        "cz": "Uživatel není specialista",
    },
    "category_beauty": {
        "en": "Beauty",
        "ru": "Красота",
        "cz": "Krása",
    },
    "category_wellness": {
        "en": "Wellness",
        "ru": "Велнес",
        "cz": "Wellness",
    },
    "category_healthcare": {
        "en": "Healthcare",
        "ru": "Здравоохранение",
        "cz": "Zdravotní péče",
    },
}
def normalize_locale(locale: str | None) -> str:
    if not locale:
        return "en"
    locale = locale.split(",")[0].split("-")[0].lower()
    # the web client says "cz", browsers say "cs" (cs-CZ) - same language
    if locale == "cs":
        locale = "cz"
    return locale if locale in SUPPORTED_LOCALES else "en"
def _(key: str, locale: str | None = None) -> str:
    loc = normalize_locale(locale)
    return messages.get(key, {}).get(loc, messages.get(key, {}).get("en", key))
