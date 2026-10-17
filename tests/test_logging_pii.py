import logging

from app.api.core.logger import mask_email, mask_phone


def test_masks():
    assert mask_email("jana.novakova@example.com") == "j***@example.com"
    assert mask_email(None) == "N/A"
    assert mask_phone("+420777123456") == "***456"


def test_login_log_has_no_email_or_phone(client, make_user, caplog):
    make_user("specialist", email="jana.secret@example.com", phone="+420777999888")
    logger = logging.getLogger("xtrix")
    logger.addHandler(caplog.handler)
    try:
        with caplog.at_level(logging.INFO, logger="xtrix"):
            client.post("/users/login", json={"email": "jana.secret@example.com", "password": "Str0ng!Pass-word"})
            client.post("/users/login", json={"phone": "+420777999888", "password": "nope-Wr0ng!x"})
    finally:
        logger.removeHandler(caplog.handler)
    text = caplog.text
    assert "Login attempt" in text
    assert "jana.secret" not in text
    assert "777999888" not in text
