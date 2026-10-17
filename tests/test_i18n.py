from app.api.core.i18n import _, normalize_locale
def test_normalize_locale():
    assert normalize_locale("ru-RU") == "ru"
    assert normalize_locale("cs") == "cz"
    assert normalize_locale("cs-CZ,cs;q=0.9") == "cz"
    assert normalize_locale("cz") == "cz"
    assert normalize_locale("en-US,en;q=0.9") == "en"
    assert normalize_locale(None) == "en"
    assert normalize_locale("fr") == "en"
def test_translations():
    assert _("phone_exists", "ru") == "Телефон уже зарегистрирован"
    assert _("phone_exists", "cs") == "Telefon je již registrován"
    assert _("phone_exists", "en") == "Phone already registered"
    assert _("unknown_key", "ru") == "unknown_key"
