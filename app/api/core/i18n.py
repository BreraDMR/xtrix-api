from typing import Dict, Any
SUPPORTED_LOCALES = ("en", "ru", "cz")
messages: Dict[str, Dict[str, str]] = {
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
    "specialist_type_massage": {
        "en": "Massage Therapist",
        "ru": "Массажист",
        "cz": "Masér",
    },
    "specialist_type_cosmetologist": {
        "en": "Cosmetologist",
        "ru": "Косметолог",
        "cz": "Kosmetička",
    },
    "specialist_type_barber": {
        "en": "Barber",
        "ru": "Барбер",
        "cz": "Holič",
    },
    "specialist_type_hairdresser": {
        "en": "Hairdresser",
        "ru": "Парикмахер",
        "cz": "Kadeřník",
    },
    "specialist_type_esthetician": {
        "en": "Esthetician",
        "ru": "Эстетист",
        "cz": "Estetička",
    },
    "specialist_type_nail_technician": {
        "en": "Nail Technician",
        "ru": "Мастер маникюра/педикюра",
        "cz": "Manikérka",
    },
    "specialist_type_lash_technician": {
        "en": "Lash & Brow Technician",
        "ru": "Мастер по ресницам и бровям",
        "cz": "Technička řas a obočí",
    },
    "specialist_type_makeup_artist": {
        "en": "Makeup Artist",
        "ru": "Визажист",
        "cz": "Vizážistka",
    },
    "specialist_type_physiotherapist": {
        "en": "Physiotherapist",
        "ru": "Физиотерапевт",
        "cz": "Fyzioterapeut",
    },
    "specialist_type_nutritionist": {
        "en": "Nutritionist",
        "ru": "Нутрициолог",
        "cz": "Výživový poradce",
    },
    "specialist_type_aromatherapist": {
        "en": "Aromatherapist",
        "ru": "Ароматерапевт",
        "cz": "Aromaterapeut",
    },
    "specialist_type_fitness_coach": {
        "en": "Fitness Coach",
        "ru": "Фитнес-тренер",
        "cz": "Fitness trenér",
    },
    "specialist_type_yoga_instructor": {
        "en": "Yoga Instructor",
        "ru": "Инструктор йоги",
        "cz": "Instruktor jógy",
    },
    "specialist_type_psychologist": {
        "en": "Psychologist",
        "ru": "Психолог",
        "cz": "Psycholog",
    },
    "specialist_type_psychotherapist": {
        "en": "Psychotherapist",
        "ru": "Психотерапевт",
        "cz": "Psychoterapeut",
    },
    "specialist_type_therapist": {
        "en": "Therapist",
        "ru": "Терапевт",
        "cz": "Terapeut",
    },
    "specialist_type_dentist": {
        "en": "Dentist",
        "ru": "Стоматолог",
        "cz": "Zubař",
    },
    "specialist_type_dermatologist": {
        "en": "Dermatologist",
        "ru": "Дерматолог",
        "cz": "Dermatolog",
    },
    "specialist_type_doctor": {
        "en": "Doctor",
        "ru": "Врач-специалист",
        "cz": "Lékař",
    },
    "specialist_type_cardiologist": {
        "en": "Cardiologist",
        "ru": "Кардиолог",
        "cz": "Kardiolog",
    },
    "specialist_type_neurologist": {
        "en": "Neurologist",
        "ru": "Невролог",
        "cz": "Neurolog",
    },
    "specialist_type_orthopedist": {
        "en": "Orthopedist",
        "ru": "Ортопед",
        "cz": "Ortoped",
    },
    "specialist_type_gynecologist": {
        "en": "Gynecologist",
        "ru": "Гинеколог",
        "cz": "Gynekolog",
    },
    "specialist_type_urologist": {
        "en": "Urologist",
        "ru": "Уролог",
        "cz": "Urolog",
    },
    "specialist_type_ophthalmologist": {
        "en": "Ophthalmologist",
        "ru": "Офтальмолог",
        "cz": "Oftalmolog",
    },
    "specialist_type_endocrinologist": {
        "en": "Endocrinologist",
        "ru": "Эндокринолог",
        "cz": "Endokrinolog",
    },
    "specialist_type_pediatrician": {
        "en": "Pediatrician",
        "ru": "Педиатр",
        "cz": "Pediatr",
    },
    "specialist_type_surgeon": {
        "en": "Surgeon",
        "ru": "Хирург",
        "cz": "Chirurg",
    },
    "specialist_type_oncologist": {
        "en": "Oncologist",
        "ru": "Онколог",
        "cz": "Onkolog",
    },
    "business_type_individual": {
        "en": "Individual Specialist",
        "ru": "Индивидуальный специалист",
        "cz": "Individuální specialista",
    },
    "business_type_salon": {
        "en": "Beauty Salon",
        "ru": "Салон красоты",
        "cz": "Kosmetický salón",
    },
    "business_type_clinic": {
        "en": "Clinic",
        "ru": "Клиника",
        "cz": "Klinika",
    },
    "business_type_hospital": {
        "en": "Hospital",
        "ru": "Больница",
        "cz": "Nemocnice",
    },
    "business_type_barbershop": {
        "en": "Barbershop",
        "ru": "Барбершоп",
        "cz": "Holičství",
    },
    "business_type_rehab_center": {
        "en": "Rehabilitation Center",
        "ru": "Реабилитационный центр",
        "cz": "Rehabilitační centrum",
    },
    "verification_status_pending": {
        "en": "Pending",
        "ru": "На проверке",
        "cz": "Čeká na ověření",
    },
    "verification_status_verified": {
        "en": "Verified",
        "ru": "Подтвержден",
        "cz": "Ověřeno",
    },
    "verification_status_rejected": {
        "en": "Rejected",
        "ru": "Отклонен",
        "cz": "Zamítnuto",
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
def localize_field(value: str, field_type: str, locale: str | None = None) -> str:
    if not value:
        return value
    key = f"{field_type}_{value.lower()}"
    translated = _(key, locale)
    return translated if translated != key else value
def localize_response(data: Any, locale: str | None = None) -> Any:
    if isinstance(data, dict):
        result = {}
        for key, value in data.items():
            if key == "role" and isinstance(value, str):
                result[key] = localize_field(value, "role", locale)
            elif key == "status" and isinstance(value, str):
                result[key] = localize_field(value, "status", locale)
            elif key == "subscription_status" and isinstance(value, str):
                result[key] = localize_field(value, "subscription", locale)
            elif key == "specialist_type" and isinstance(value, str):
                result[key] = localize_field(value, "specialist_type", locale)
            elif key == "specialist_category" and isinstance(value, str):
                result[key] = localize_field(value, "category", locale)
            elif key == "business_type" and isinstance(value, str):
                result[key] = localize_field(value, "business_type", locale)
            elif key == "is_verified" and isinstance(value, str):
                result[key] = localize_field(value, "verification_status", locale)
            elif isinstance(value, (dict, list)):
                result[key] = localize_response(value, locale)
            else:
                result[key] = value
        return result
    elif isinstance(data, list):
        return [localize_response(item, locale) for item in data]
    return data
