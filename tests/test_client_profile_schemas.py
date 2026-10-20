import pytest
from pydantic import ValidationError

from app.schemas.client_profile import (
    MAX_NOTE_LENGTH,
    MAX_PREFERENCES_LENGTH,
    MAX_TAGS,
    ClientNoteCreate,
    ClientNoteUpdate,
    ClientProfileUpdate,
)


def error_code(exc: ValidationError) -> str:
    return exc.errors()[0]["type"]


def test_note_body_is_limited_and_must_be_plain_text():
    assert ClientNoteCreate(body="Prefers a quiet room").body == "Prefers a quiet room"
    with pytest.raises(ValidationError) as too_long:
        ClientNoteCreate(body="x" * (MAX_NOTE_LENGTH + 1))
    assert error_code(too_long.value) == "string_too_long"
    with pytest.raises(ValidationError) as html:
        ClientNoteCreate(body="<b>VIP</b>")
    assert error_code(html.value) == "html_not_allowed"
    with pytest.raises(ValidationError) as empty:
        ClientNoteUpdate(body="   ")
    assert error_code(empty.value) == "string_too_short"


def test_tags_are_short_unique_and_plain():
    profile = ClientProfileUpdate(tags=["  vip ", "morning", "VIP 2"])
    assert profile.tags == ["vip", "morning", "VIP 2"]

    with pytest.raises(ValidationError) as too_many:
        ClientProfileUpdate(tags=[f"tag-{index}" for index in range(MAX_TAGS + 1)])
    assert error_code(too_many.value) == "too_long"

    with pytest.raises(ValidationError) as duplicate:
        ClientProfileUpdate(tags=["VIP", "vip"])
    assert error_code(duplicate.value) == "duplicate_tag"

    with pytest.raises(ValidationError) as empty:
        ClientProfileUpdate(tags=["vip", "  "])
    assert error_code(empty.value) == "empty_tag"

    with pytest.raises(ValidationError) as html:
        ClientProfileUpdate(tags=["<script>alert(1)</script>"])
    assert error_code(html.value) == "html_not_allowed"


def test_preferences_are_plain_text_and_nullable():
    assert ClientProfileUpdate(preferences="  Prefers mornings  ").preferences == "Prefers mornings"
    assert ClientProfileUpdate(preferences="   ").preferences is None
    assert ClientProfileUpdate(preferences=None).preferences is None
    with pytest.raises(ValidationError) as too_long:
        ClientProfileUpdate(preferences="x" * (MAX_PREFERENCES_LENGTH + 1))
    assert error_code(too_long.value) == "string_too_long"
    with pytest.raises(ValidationError) as html:
        ClientProfileUpdate(preferences="<a href='#'>link</a>")
    assert error_code(html.value) == "html_not_allowed"


def test_profile_schemas_do_not_accept_healthcare_fields():
    with pytest.raises(ValidationError):
        ClientNoteCreate(body="ok", allergies="latex")
    with pytest.raises(ValidationError):
        ClientProfileUpdate(tags=[], pregnancy=True)
