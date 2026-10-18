"""422 items carry a stable `code` (+ `params`) so the web app can translate them itself."""
import pytest


def _errors(r):
    assert r.status_code == 422, r.text
    return {e["field"].split(" -> ")[-1]: e for e in r.json()["error"]["detail"]["validation_errors"]}


def test_builtin_errors_carry_code_and_params(client, auth_headers):
    e = _errors(client.post("/clients/", json={"full_name": ""}, headers=auth_headers))["full_name"]
    assert e["code"] == "string_too_short"
    assert e["params"] == {"min_length": 1}
    assert e["field"] == "body -> full_name"  # the old keys stay as they were
    assert e["message"]


def test_missing_field_has_a_code(client, auth_headers):
    e = _errors(client.post("/clients/", json={}, headers=auth_headers))["full_name"]
    assert e["code"] == "missing"


@pytest.mark.parametrize("body, field, code", [
    ({"full_name": "A", "gender": "robot"}, "gender", "invalid_choice"),
    ({"full_name": "A", "contact_phone": "12"}, "contact_phone", "invalid_phone"),
    ({"full_name": "A", "birth_date": "2999-01-01"}, "birth_date", "invalid_birth_date"),
])
def test_our_own_checks_have_their_own_codes(client, auth_headers, body, field, code):
    e = _errors(client.post("/clients/", json=body, headers=auth_headers))[field]
    assert e["code"] == code
    assert not e["message"].startswith("Value error")


def test_choice_error_lists_allowed_values(client, auth_headers):
    e = _errors(client.post("/clients/", json={"full_name": "A", "gender": "robot"}, headers=auth_headers))["gender"]
    assert e["params"]["allowed"] == ["male", "female", "other"]


def test_signup_codes(client):
    errors = _errors(client.post("/users/", json={
        "full_name": "A", "email": "a@example.com", "password": "short", "role": "specialist"}))
    assert errors["password"]["code"] in ("string_too_short", "weak_password")
    errors = _errors(client.post("/users/", json={
        "full_name": "A", "email": "a@example.com", "password": "abcdefgh1", "role": "specialist"}))
    assert errors["password"]["code"] == "weak_password"


def test_params_are_plain_json(client, make_user, headers_for):
    # model-level checks used to put the exception object into ctx
    owner = make_user("business_owner")
    r = client.post("/loyalty/promotions", headers=headers_for(owner), json={
        "name": "P", "promotion_type": "bonus_points", "bonus_points": 1, "program_id": 1,
        "start_date": "2030-02-01", "end_date": "2030-01-01"})
    items = r.json()["error"]["detail"]["validation_errors"]
    assert r.status_code == 422, r.text
    assert [e["code"] for e in items] == ["end_before_start"]
    assert "error" not in items[0]["params"]


def test_create_with_user_texts_are_translated(client, make_user, headers_for):
    owner = make_user("business_owner")
    h = {**headers_for(owner), "Accept-Language": "ru"}
    r = client.post("/employees/create-with-user", json={"full_name": "A", "phone": "12"}, headers=h)
    assert r.status_code == 400
    assert r.json()["error"]["message"] == "Укажите корректный телефон или email"
    r = client.post("/employees/create-with-user", json={"full_name": "A", "email": "not-an-email"}, headers=h)
    assert r.status_code == 400
    assert r.json()["error"]["message"] == "Проверьте имя, телефон и email"
