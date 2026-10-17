"""E-mails and SMS to staff users (owners, admins) in their UI language.

The language lives in UserPreferences.language; missing means Russian, like before.
Where to write is also taken from preferences: notification_email overrides the
login e-mail, and email_notifications=False turns e-mails off.
"""
from datetime import datetime
from typing import NamedTuple, Optional

from sqlalchemy.orm import Session

from app.api.core.client_messages import Message

FALLBACK = "ru"
LANGS = ("en", "ru", "cz")


class StaffContact(NamedTuple):
    email: Optional[str]
    phone: Optional[str]
    language: str


def contact_for(db: Session, user) -> StaffContact:
    from app.db.models.user_preferences import UserPreferences

    prefs = db.query(UserPreferences).filter(UserPreferences.user_id == user.id).first()
    language = prefs.language if prefs and prefs.language in LANGS else FALLBACK
    email = (prefs.notification_email if prefs and prefs.notification_email else None) or user.email
    if prefs and prefs.email_notifications is False:
        email = None
    return StaffContact(email, user.phone, language)


def _when_text(days: int, lang: str) -> str:
    if days == 0:
        return {"en": "today", "ru": "сегодня", "cz": "dnes"}[lang]
    if days == 1:
        return {"en": "tomorrow", "ru": "завтра", "cz": "zítra"}[lang]
    return {"en": f"in {days} days", "ru": f"через {days} дн.", "cz": f"za {days} dní"}[lang]


def subscription_expiry(lang: str, name: str, expiry: datetime, days: int) -> Message:
    lang = lang if lang in LANGS else FALLBACK
    when, date = _when_text(days, lang), expiry.strftime("%d.%m.%Y")
    if lang == "en":
        subject = f"Your subscription expires {when}"
        body = (f"Hello {name},\n\nyour Xtrix subscription expires {when} ({date}).\n"
                "To keep using the service, open \"Subscription\" in your account and renew the plan.\n\n"
                "Questions? Just reply to this e-mail.\n\nBest regards,\nthe Xtrix team")
        short = f"Xtrix: your subscription expires {when} ({date}). Renew it in your account."
    elif lang == "cz":
        subject = f"Vaše předplatné končí {when}"
        body = (f"Dobrý den, {name},\n\nVaše předplatné Xtrix končí {when} ({date}).\n"
                "Pro další používání služby otevřete v účtu sekci „Předplatné“ a tarif obnovte.\n\n"
                "Máte dotaz? Stačí odpovědět na tento e-mail.\n\nS pozdravem,\ntým Xtrix")
        short = f"Xtrix: Vaše předplatné končí {when} ({date}). Obnovte ho ve svém účtu."
    else:
        subject = f"Ваша подписка истекает {when}"
        body = (f"Здравствуйте, {name}!\n\nВаша подписка на Xtrix истекает {when} ({date}).\n"
                "Чтобы продолжить пользоваться сервисом, откройте раздел «Подписка» в личном кабинете и продлите план.\n\n"
                "Есть вопросы? Просто ответьте на это письмо.\n\nС уважением,\nкоманда Xtrix")
        short = f"Xtrix: ваша подписка истекает {when} ({date}). Продлите её в личном кабинете."
    return Message(subject, body, short)


def low_stock(lang: str, name: str, business_name: str, products) -> Message:
    lang = lang if lang in LANGS else FALLBACK
    minimum = {"en": "minimum", "ru": "минимум", "cz": "minimum"}[lang]
    lines = "\n".join(f"- {p.name}: {p.quantity} {p.unit} ({minimum}: {p.min_quantity} {p.unit})" for p in products)
    count = len(products)
    if lang == "en":
        subject = f"⚠️ Low stock: {count} product(s)"
        body = (f"Hello {name},\n\nthese products are running low at \"{business_name}\":\n{lines}\n\n"
                "Please restock in time. Details are in \"Inventory\" in your account.\n\nBest regards,\nthe Xtrix team")
    elif lang == "cz":
        subject = f"⚠️ Docházející zásoby: {count} položek"
        body = (f"Dobrý den, {name},\n\nv „{business_name}“ docházejí tyto položky:\n{lines}\n\n"
                "Prosíme, doplňte je včas. Podrobnosti najdete v sekci „Sklad“ ve svém účtu.\n\nS pozdravem,\ntým Xtrix")
    else:
        subject = f"⚠️ Низкий остаток на складе: {count} поз."
        body = (f"Здравствуйте, {name}!\n\nНа складе «{business_name}» заканчиваются товары:\n{lines}\n\n"
                "Пожалуйста, пополните склад вовремя. Подробности — в разделе «Склад» личного кабинета.\n\nС уважением,\nкоманда Xtrix")
    return Message(subject, body, subject)
