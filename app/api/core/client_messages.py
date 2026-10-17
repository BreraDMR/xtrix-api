"""Texts we send to a business's clients (reminders, greetings), in the client's own language.

The language comes from Client.language and has nothing to do with the staff UI language.
Unknown or missing language falls back to Russian - that's what these messages used to be in.
"""
from datetime import datetime
from typing import NamedTuple, Optional

FALLBACK = "ru"


class Message(NamedTuple):
    subject: str
    body: str  # email text
    short: str  # sms / whatsapp


_SIGN = {
    "en": "Best regards,\nthe {team} team",
    "ru": "С уважением,\nкоманда {team}",
    "cz": "S pozdravem,\ntým {team}",
}

_TEXTS = {
    "reminder": {
        "en": ("Appointment reminder",
               "Hello {name},\n\nthis is a reminder of your appointment on {when}.\nService: {service}\n\n{sign}",
               "Reminder: your appointment on {when}, {service}. {team}"),
        "ru": ("Напоминание о визите",
               "Здравствуйте, {name}!\n\nНапоминаем о вашем визите {when}.\nУслуга: {service}\n\n{sign}",
               "Напоминание: визит {when}, {service}. {team}"),
        "cz": ("Připomínka návštěvy",
               "Dobrý den, {name},\n\npřipomínáme Vaši návštěvu {when}.\nSlužba: {service}\n\n{sign}",
               "Připomínka: návštěva {when}, {service}. {team}"),
    },
    "birthday": {
        "en": ("🎉 Happy birthday!",
               "Dear {name},\n\nhappy birthday! 🎂🎁\nWe wish you good health, happiness and all the best!\n\n{sign}",
               "🎉 Dear {name}, happy birthday! Wishing you health and happiness. {team}"),
        "ru": ("🎉 С днём рождения!",
               "Дорогая(ой) {name}!\n\nПоздравляем Вас с днём рождения! 🎂🎁\nЖелаем крепкого здоровья, счастья и всего самого наилучшего!\n\n{sign}",
               "🎉 Дорогая(ой) {name}! Поздравляем с днём рождения! Желаем здоровья и счастья. {team}"),
        "cz": ("🎉 Všechno nejlepší k narozeninám!",
               "Milá/milý {name},\n\nvšechno nejlepší k narozeninám! 🎂🎁\nPřejeme Vám hodně zdraví, štěstí a pohody!\n\n{sign}",
               "🎉 {name}, všechno nejlepší k narozeninám! Hodně zdraví a štěstí. {team}"),
    },
    "followup": {
        "en": ("Thank you for your visit!",
               "Dear {name},\n\nthank you for visiting us on {when}!\nWe hope you enjoyed \"{service}\". We'd be glad to see you again!\n\n{sign}",
               "Thank you for your visit on {when}! We hope you enjoyed {service}. {team}"),
        "ru": ("Спасибо за визит!",
               "Дорогая(ой) {name}!\n\nСпасибо, что посетили нас {when}!\nНадеемся, что услуга «{service}» Вам понравилась. Будем рады видеть Вас снова!\n\n{sign}",
               "Спасибо за визит {when}! Надеемся, услуга «{service}» Вам понравилась. {team}"),
        "cz": ("Děkujeme za návštěvu!",
               "Milá/milý {name},\n\nděkujeme, že jste nás {when} navštívil(a)!\nDoufáme, že jste byl(a) se službou „{service}“ spokojen(a). Těšíme se na další návštěvu!\n\n{sign}",
               "Děkujeme za návštěvu {when}! Doufáme, že jste byl(a) se službou {service} spokojen(a). {team}"),
    },
    "loyalty": {
        "en": ("Loyalty programme", "Dear {name},\n\n{text}\n\n{sign}", "{name}, {text} {team}"),
        "ru": ("Программа лояльности", "Дорогая(ой) {name}!\n\n{text}\n\n{sign}", "{name}, {text} {team}"),
        "cz": ("Věrnostní program", "Milá/milý {name},\n\n{text}\n\n{sign}", "{name}, {text} {team}"),
    },
    "verification": {
        "en": ("Verification code", "Your verification code: {text}", "Your verification code: {text}"),
        "ru": ("Код подтверждения", "Ваш код подтверждения: {text}", "Ваш код подтверждения: {text}"),
        "cz": ("Ověřovací kód", "Váš ověřovací kód: {text}", "Váš ověřovací kód: {text}"),
    },
}


def language_of(client) -> str:
    lang = getattr(client, "language", None)
    return lang if lang in _SIGN else FALLBACK


def _when(value) -> str:
    return value.strftime("%d.%m.%Y %H:%M") if isinstance(value, datetime) else str(value or "")


def render(kind: str, lang: Optional[str], *, name: str = "", when=None, service: str = "",
           business_name: Optional[str] = None, text: str = "") -> Message:
    lang = lang if lang in _SIGN else FALLBACK
    team = business_name or "Xtrix"
    subject, body, short = _TEXTS[kind][lang]
    values = {"name": name, "when": _when(when), "service": service or "", "team": team, "text": text}
    values["sign"] = _SIGN[lang].format(team=team)
    return Message(subject.format(**values), body.format(**values), short.format(**values))
