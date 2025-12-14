from app.api.core.i18n import _, normalize_locale


def test_normalize_locale():
    assert normalize_locale("ru-RU") == "ru"
    assert normalize_locale("cs") == "cs"
    assert normalize_locale("en-US,en;q=0.9") == "en"
    assert normalize_locale(None) == "en"
    assert normalize_locale("fr") == "en"


def test_translations():
    assert _("phone_exists", "ru") == "Телефон уже зарегистрирован"
    assert _("phone_exists", "cs") == "Telefon je již registrován"
    assert _("phone_exists", "en") == "Phone already registered"
    # fallback to en
    assert _("unknown_key", "ru") == "unknown_key"











