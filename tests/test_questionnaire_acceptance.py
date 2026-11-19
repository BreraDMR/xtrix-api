"""FORM-12 acceptance: visitor questionnaires checked over HTTP, card by card.

test_questionnaires.py already covers the happy path, expiry, tenant-swapped tokens and
most of the upload flow. This file adds the "done" lines it doesn't touch, so a failure
points at the FORM card it breaks. Synthetic salons, example.com contacts, nothing sent.
"""
import base64
import json
import logging
from concurrent.futures import ThreadPoolExecutor

import boto3

from app.api.core import s3 as s3_core
from app.db.models.employee import Employee
from app.db.models.questionnaire import QuestionnaireVersion
from app.db.models.questionnaire_access import QuestionnaireAccessToken
from app.db.models.questionnaire_response import QuestionnaireResponse
from app.db.models.user import User
from app.db.models.visit import Visit
from tests.test_public_booking_acceptance import _body, _book, _local_utc
from tests.test_public_booking_acceptance import _salon as _booking_salon
from tests.test_questionnaires import ANSWERS, FIELDS, _guest, _ok, _published_token, _salon

TEMPLATES = "/businesses/me/questionnaires/"


def _text(key, **extra):
    return {"key": key, "label": key.title(), "type": "text", **extra}


def _choice(key, kind="single_choice", count=2, **extra):
    options = [{"value": f"o{i}", "label": f"Option {i}"} for i in range(count)]
    return {"key": key, "label": key.title(), "type": kind, "options": options, **extra}


def _employee(client, db, salon, name):
    created = _ok(client.post("/employees/create-with-user", json={
        "full_name": f"{name} Master", "email": f"{name.lower()}.master@example.com",
        "phone": "+420777123456", "position": "Stylist", "base_salary": 1000,
    }, headers=salon["headers"]), "employee")
    created = created.get("employee", created)
    user_id = created.get("employee_user_id") or created.get("user_id")
    return db.query(User).filter(User.id == user_id).one()


# FORM-01: bounded plain-data fields, no HTML and no executable rules

def test_template_rejects_html_rules_and_oversize(client, make_user, headers_for):
    salon = _salon(client, make_user, headers_for, "Limits")
    bad_field_lists = {
        "51 fields": [_text(f"f{i}") for i in range(51)],
        "21 options": [_choice("pick", count=21)],
        "one option": [_choice("pick", count=1)],
        "html label": [{**_text("name"), "label": "<b>Name</b>"}],
        "control char": [{**_text("name"), "label": "Na\x00me"}],
        "regex rule": [_text("name", pattern="^.*$")],
        "script type": [{"key": "run", "label": "Run", "type": "script"}],
        "file type": [{"key": "scan", "label": "Scan", "type": "file"}],
        "duplicate keys": [_text("name"), _text("name")],
        "bad key": [{**_text("name"), "key": "Bad Key"}],
        "text too long": [_text("name", max_length=256)],
        "max_selected over options": [_choice("pick", "multi_choice", count=2, max_selected=3)],
        "html option": [{**_choice("pick"), "options": [
            {"value": "a", "label": "<i>A</i>"}, {"value": "b", "label": "B"}]}],
    }
    for case, fields in bad_field_lists.items():
        response = client.post(TEMPLATES, json={"name": "Bad", "fields": fields}, headers=salon["headers"])
        assert response.status_code == 422, f"{case}: {response.status_code} {response.text[:200]}"
    assert client.post(TEMPLATES, json={"name": "<script>", "fields": []}, headers=salon["headers"]).status_code == 422

    full = client.post(TEMPLATES, json={
        "name": "Full", "fields": [_text(f"f{i}") for i in range(49)] + [_choice("pick", count=20)],
    }, headers=salon["headers"])
    assert full.status_code == 200, full.text
    assert len(full.json()["fields"]) == 50
    assert client.get(TEMPLATES, headers=salon["headers"]).json()[0]["name"] == "Full"


# FORM-02/04: editing the draft never rewrites a published version or old answers

def test_published_version_and_answers_survive_draft_edits(client, db, make_user, headers_for):
    salon = _salon(client, make_user, headers_for, "Frozen")
    issued = _published_token(client, salon)
    template_id = issued["template"]["id"]
    visit_id = salon["visit"]["id"]
    _ok(client.post("/public/questionnaires/answers", json={"answers": ANSWERS},
                    headers=_guest(issued["token"])), "answers")

    edited = _ok(client.patch(f"{TEMPLATES}{template_id}", json={
        "name": "Renamed", "fields": [{**FIELDS[0], "label": "Changed wording"}, FIELDS[2]],
    }, headers=salon["headers"]), "edit draft")
    assert edited["published_version"] == 1
    assert [field["key"] for field in edited["fields"]] == ["symptoms", "consent"]

    def card():
        return _ok(client.get(f"/visits/{visit_id}/questionnaire", headers=salon["headers"]), "card")

    before_republish = card()
    assert before_republish["version"] == 1 and before_republish["name"] == "Intake"
    assert before_republish["fields"][0]["label"] == "Symptoms"
    assert before_republish["answers"] == {"symptoms": "headache", "mood": "good", "consent": True}

    v2 = _ok(client.post(f"{TEMPLATES}{template_id}/publish", headers=salon["headers"]), "publish v2")
    assert v2["version"] == 2 and v2["service_id"] == salon["service"]["id"]
    assert card() == before_republish

    # the old link stays on the version it was issued for
    old_form = _ok(client.get("/public/questionnaires", headers=_guest(issued["token"])), "old form")
    assert old_form["version"] == 1
    assert [field["label"] for field in old_form["fields"]] == ["Symptoms", "Mood", "Consent"]

    # a fresh link gets v2, but the visit is already answered: no second response
    fresh = _ok(client.post(f"/visits/{visit_id}/questionnaire-token", headers=salon["headers"]), "v2 token")
    assert fresh["version"] == 2
    again = client.post("/public/questionnaires/answers",
                        json={"answers": {"symptoms": "x", "consent": True}}, headers=_guest(fresh["token"]))
    assert again.status_code == 409, again.text
    db.expire_all()
    assert db.query(QuestionnaireResponse).filter_by(visit_id=visit_id).count() == 1
    v1 = db.query(QuestionnaireVersion).filter_by(template_id=template_id, version=1).one()
    assert v1.name == "Intake" and [field["key"] for field in v1.fields] == ["symptoms", "mood", "consent"]


def test_empty_or_broken_draft_cannot_be_published(client, db, make_user, headers_for):
    salon = _salon(client, make_user, headers_for, "Empty")
    empty = _ok(client.post(TEMPLATES, json={"name": "Empty", "fields": []}, headers=salon["headers"]), "empty")
    assert client.post(f"{TEMPLATES}{empty['id']}/publish", headers=salon["headers"]).status_code == 422

    issued = _published_token(client, salon, name="Was fine")
    template_id = issued["template"]["id"]
    _ok(client.patch(f"{TEMPLATES}{template_id}", json={"fields": []}, headers=salon["headers"]), "clear")
    assert client.post(f"{TEMPLATES}{template_id}/publish", headers=salon["headers"]).status_code == 422
    assert client.patch(f"{TEMPLATES}{template_id}", json={"fields": None},
                        headers=salon["headers"]).status_code == 422
    assert client.patch(f"{TEMPLATES}{template_id}", json={}, headers=salon["headers"]).status_code == 422
    db.expire_all()
    assert db.query(QuestionnaireVersion).filter_by(template_id=template_id).count() == 1
    assert db.query(QuestionnaireVersion).filter_by(template_id=empty["id"]).count() == 0


# FORM-03/04: owner-level config; foreign ids look like missing ones

def test_owner_scope_staff_and_foreign_ids(client, db, make_user, headers_for):
    alpha = _salon(client, make_user, headers_for, "ScopeAlpha")
    beta = _salon(client, make_user, headers_for, "ScopeBeta")
    template_id = _published_token(client, alpha)["template"]["id"]

    for method, path, body in (
        ("patch", f"{TEMPLATES}{template_id}", {"name": "Stolen"}),
        ("post", f"{TEMPLATES}{template_id}/publish", None),
        ("get", f"{TEMPLATES}{template_id}", None),
    ):
        response = getattr(client, method)(path, json=body, headers=beta["headers"]) if body else \
            getattr(client, method)(path, headers=beta["headers"])
        assert response.status_code == 404, f"{method} {path}: {response.status_code}"
    assert client.get(TEMPLATES, headers=beta["headers"]).json() == []
    assert _ok(client.get(f"{TEMPLATES}{template_id}", headers=alpha["headers"]), "own")["name"] == "Intake"

    publish = f"{TEMPLATES}{template_id}/publish"
    assert client.post(publish, json={"service_id": beta["service"]["id"]}, headers=alpha["headers"]).status_code == 404
    assert client.post(publish, json={"service_id": 999999}, headers=alpha["headers"]).status_code == 404
    assert client.patch(f"{TEMPLATES}{template_id}", json={"fields": [{"key": "x", "label": "X", "type": "html"}]},
                        headers=alpha["headers"]).status_code == 422

    other = _ok(client.post(TEMPLATES, json={"name": "Other", "fields": FIELDS}, headers=alpha["headers"]), "other")
    taken = client.post(f"{TEMPLATES}{other['id']}/publish", json={"service_id": alpha["service"]["id"]},
                        headers=alpha["headers"])
    assert taken.status_code == 409, taken.text

    assert _ok(client.post(publish, headers=alpha["headers"]), "keep")["service_id"] == alpha["service"]["id"]
    assert _ok(client.post(publish, json={"service_id": None}, headers=alpha["headers"]), "unbind")["service_id"] is None
    # unbound: the visit no longer has a questionnaire to hand out
    unavailable = client.post(f"/visits/{alpha['visit']['id']}/questionnaire-token", headers=alpha["headers"])
    assert unavailable.status_code == 404, unavailable.text

    staff = headers_for(_employee(client, db, alpha, "ScopeStaff"))
    assert client.get(TEMPLATES, headers=staff).status_code == 403
    assert client.post(TEMPLATES, json={"name": "Staff", "fields": FIELDS}, headers=staff).status_code == 403
    assert client.patch(f"{TEMPLATES}{template_id}", json={"name": "Staff"}, headers=staff).status_code == 403


# Rights on answers: owner and the visit's own master read them, other staff don't

def test_staff_reads_answers_only_for_visits_they_may_see(client, db, make_user, headers_for):
    salon = _booking_salon(client, make_user, headers_for, "Rights")
    owner_headers = salon["headers"]
    _ok(client.post(TEMPLATES, json={"name": "Intake", "fields": FIELDS}, headers=owner_headers), "template")
    template_id = client.get(TEMPLATES, headers=owner_headers).json()[0]["id"]
    _ok(client.post(f"{TEMPLATES}{template_id}/publish", json={"service_id": salon["service"]["id"]},
                    headers=owner_headers), "publish")
    def guest(name):
        return _ok(client.post("/clients/", json={
            "full_name": f"{name} Guest", "contact_email": f"{name.lower()}.guest@example.com",
        }, headers=owner_headers), "client")

    def visit(client_id, specialist_id, hour):
        start, _day = _local_utc(days_ahead=6, hour=hour)
        return _ok(client.post("/visits/", json={
            "client_id": client_id, "specialist_id": specialist_id, "service_id": salon["service"]["id"],
            "date_start": start, "price": 450,
        }, headers=owner_headers), "visit")

    mine, theirs = guest("Mine"), guest("Theirs")
    own = visit(mine["id"], salon["employee_id"], 10)
    # another guest the master has never served, booked with the owner
    foreign = visit(theirs["id"], salon["owner"].id, 12)
    staff = headers_for(db.query(User).filter(User.id == salon["employee_id"]).one())

    issued = _ok(client.post(f"/visits/{own['id']}/questionnaire-token", headers=staff), "staff issues own")
    _ok(client.post("/public/questionnaires/answers", json={"answers": ANSWERS},
                    headers=_guest(issued["token"])), "answers")
    assert _ok(client.get(f"/visits/{own['id']}/questionnaire", headers=staff), "own card")["version"] == 1

    owner_token = _ok(client.post(f"/visits/{foreign['id']}/questionnaire-token", headers=owner_headers), "owner")
    _ok(client.post("/public/questionnaires/answers", json={"answers": ANSWERS},
                    headers=_guest(owner_token["token"])), "answers 2")
    # unrestricted staff see every client of the salon (the general client-access rule)
    assert client.get(f"/visits/{foreign['id']}/questionnaire", headers=staff).status_code == 200

    employee_row = db.query(Employee).filter(Employee.employee_user_id == salon["employee_id"]).one()
    _ok(client.put(f"/employees/{employee_row.id}", json={"permissions": {"assigned_client_ids": [mine["id"]]}},
                   headers=owner_headers), "restrict staff")
    for response in (
        client.get(f"/visits/{foreign['id']}/questionnaire", headers=staff),
        client.get(f"/visits/{foreign['id']}/questionnaire/attachments", headers=staff),
        client.post(f"/visits/{foreign['id']}/questionnaire-token", headers=staff),
    ):
        assert response.status_code == 403, response.text
        assert "headache" not in response.text
    assert _ok(client.get(f"/visits/{own['id']}/questionnaire", headers=staff), "own after restrict")["version"] == 1
    assert _ok(client.get(f"/visits/{foreign['id']}/questionnaire", headers=owner_headers), "owner card")["answers"]


# FORM-05: token bound to one visit and version, never in logs, dies with the visit

def test_token_is_scoped_unlogged_and_cannot_be_redirected(client, db, make_user, headers_for, caplog):
    caplog.set_level(logging.DEBUG)
    salon = _salon(client, make_user, headers_for, "Token")
    issued = _published_token(client, salon)
    raw = issued["token"]
    guest = _guest(raw)

    for extra in ({"visit_id": 1}, {"version": 2}, {"version_id": 1}, {"token": raw}):
        response = client.post("/public/questionnaires/answers", json={"answers": ANSWERS, **extra}, headers=guest)
        assert response.status_code == 422, f"{extra}: {response.text[:200]}"
    assert client.get("/public/questionnaires", headers=_guest(raw + "x")).status_code == 404
    assert client.get("/public/questionnaires", headers=_guest("a" * 129)).status_code == 404
    assert client.get("/public/questionnaires").status_code == 404
    assert client.get(f"/public/questionnaires?token={raw}").status_code == 404
    _ok(client.post("/public/questionnaires/answers", json={"answers": ANSWERS}, headers=guest), "answers")

    db.expire_all()
    row = db.query(QuestionnaireAccessToken).filter_by(visit_id=salon["visit"]["id"]).one()
    assert row.token_hash != raw and raw not in row.token_hash
    # httpx is the test's own client logging the URL it was asked to fetch; the app must not
    leaked = [r.name for r in caplog.records if raw in r.getMessage() and not r.name.startswith("httpx")]
    assert leaked == []

    deleted = client.delete(f"/visits/{salon['visit']['id']}", headers=salon["headers"])
    assert deleted.status_code in (200, 204), deleted.text
    db.expire_all()
    assert db.query(Visit).filter_by(id=salon["visit"]["id"]).one().deleted_at is not None
    assert client.get("/public/questionnaires", headers=guest).status_code == 404
    assert client.post("/public/questionnaires/answers", json={"answers": ANSWERS}, headers=guest).status_code == 404
    assert client.post(f"/visits/{salon['visit']['id']}/questionnaire-token",
                       headers=salon["headers"]).status_code == 404


# FORM-06: the server owns validation, retries collapse into one response

def test_server_validates_every_field_type(client, db, make_user, headers_for):
    salon = _salon(client, make_user, headers_for, "Types")
    fields = [
        _text("short", required=True, max_length=10),
        {"key": "notes", "label": "Notes", "type": "long_text", "max_length": 50},
        _choice("one", required=True),
        _choice("many", "multi_choice", count=3, max_selected=2),
        {"key": "agree", "label": "Agree", "type": "boolean", "required": True},
    ]
    template = _ok(client.post(TEMPLATES, json={"name": "Types", "fields": fields}, headers=salon["headers"]), "t")
    _ok(client.post(f"{TEMPLATES}{template['id']}/publish", json={"service_id": salon["service"]["id"]},
                    headers=salon["headers"]), "publish")
    token = _ok(client.post(f"/visits/{salon['visit']['id']}/questionnaire-token", headers=salon["headers"]), "tok")
    guest = _guest(token["token"])
    good = {"short": "ok", "notes": "line one\nline two", "one": "o0", "many": ["o1", "o2"], "agree": False}

    cases = {
        "short": ({"short": "x" * 11}, "too_long"),
        "notes": ({"notes": "bad\x00char"}, "invalid_text"),
        "one": ({"one": "o9"}, "invalid_option"),
        "many": ({"many": ["o0", "o1", "o2"]}, "too_many_selected"),
        "agree": ({"agree": "yes"}, "expected_boolean"),
        "spare": ({"spare": "x"}, "unknown_field"),
    }
    for key, (patch, code) in cases.items():
        response = client.post("/public/questionnaires/answers", json={"answers": {**good, **patch}}, headers=guest)
        assert response.status_code == 422, f"{key}: {response.text[:200]}"
        assert response.json()["error"]["detail"]["fields"][key] == code, key
    for key, value, code in (("many", ["o1", "o1"], "duplicate_option"), ("many", "o1", "expected_list"),
                             ("short", "   ", "required"), ("agree", None, "required")):
        response = client.post("/public/questionnaires/answers", json={"answers": {**good, key: value}}, headers=guest)
        assert response.json()["error"]["detail"]["fields"][key] == code, (key, value)
    db.expire_all()
    assert db.query(QuestionnaireResponse).filter_by(visit_id=salon["visit"]["id"]).count() == 0

    def submit(_):
        return client.post("/public/questionnaires/answers", json={"answers": good}, headers=guest)

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(submit, range(4)))
    assert {result.status_code for result in results} == {200}, [r.text for r in results]
    assert len({result.json()["submitted_at"] for result in results}) == 1
    db.expire_all()
    stored = db.query(QuestionnaireResponse).filter_by(visit_id=salon["visit"]["id"]).one()
    assert stored.answers == good  # false is a real answer to a required yes/no question


# FORM-07: the upload policy is private, typed and size-bounded; no client storage key

def test_presigned_upload_policy_is_private_and_bounded(client, make_user, headers_for, monkeypatch):
    monkeypatch.setattr(s3_core.settings, "aws_bucket", "xtrix-acceptance-test")
    monkeypatch.setattr(s3_core, "s3", boto3.client(
        "s3", region_name="eu-central-1", aws_access_key_id="AKIAEXAMPLEEXAMPLE",
        aws_secret_access_key="example-secret-not-real",
    ))
    salon = _salon(client, make_user, headers_for, "Policy")
    guest = _guest(_published_token(client, salon)["token"])

    sneaky = client.post("/public/questionnaires/attachments", json={
        "filename": "scan.pdf", "content_type": "application/pdf", "size_bytes": 100,
        "storage_key": f"questionnaires/{salon['business']['id']}/1/other.pdf",
    }, headers=guest)
    assert sneaky.status_code == 422, sneaky.text

    created = _ok(client.post("/public/questionnaires/attachments", json={
        "filename": "../../etc/scan.pdf", "content_type": "application/pdf", "size_bytes": 100,
    }, headers=guest), "reserve")
    fields = created["upload_fields"]
    key = fields["key"]
    assert key.startswith(f"questionnaires/{salon['business']['id']}/{salon['visit']['id']}/")
    assert key.endswith(".pdf") and "scan" not in key and ".." not in key
    assert "acl" not in {name.lower() for name in fields}
    policy = json.loads(base64.b64decode(fields["policy"]))
    conditions = policy["conditions"]
    assert {"bucket": "xtrix-acceptance-test"} in conditions
    assert {"key": key} in conditions
    assert {"Content-Type": "application/pdf"} in conditions
    assert ["content-length-range", 1, 10 * 1024 * 1024] in conditions
    assert not any(isinstance(item, dict) and "acl" in item for item in conditions)


# FORM-12: a questionnaire on the service never blocks a public booking

def test_public_booking_does_not_wait_for_a_questionnaire(client, db, make_user, headers_for):
    salon = _booking_salon(client, make_user, headers_for, "NoGate")
    template = _ok(client.post(TEMPLATES, json={"name": "Intake", "fields": FIELDS}, headers=salon["headers"]), "t")
    _ok(client.post(f"{TEMPLATES}{template['id']}/publish", json={"service_id": salon["service"]["id"]},
                    headers=salon["headers"]), "publish")
    start, _day = _local_utc(days_ahead=5, hour=11)
    booked = _book(client, salon, _body(salon, start, "nogate-booking-1"))
    assert booked.status_code == 200, booked.text
    db.expire_all()
    visits = db.query(Visit).filter(Visit.business_id == salon["business"]["id"]).all()
    assert len(visits) == 1
    # no automatic link and no answers needed: the owner hands a link out by hand
    assert db.query(QuestionnaireAccessToken).filter_by(visit_id=visits[0].id).count() == 0
    issued = _ok(client.post(f"/visits/{visits[0].id}/questionnaire-token", headers=salon["headers"]), "token")
    assert issued["version"] == 1
