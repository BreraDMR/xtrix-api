"""Questionnaire tokens and answers: replay, expiry, tenancy and private uploads."""
from datetime import datetime, timedelta

from app.api.core import questionnaire_attachments as attachment_core
from app.api.core.clock import utcnow
from app.db.models.questionnaire_access import QuestionnaireAccessToken
from app.db.models.questionnaire_attachment import QuestionnaireAttachment

FIELDS = [
    {"key": "symptoms", "label": "Symptoms", "type": "text", "required": True, "max_length": 200},
    {
        "key": "mood",
        "label": "Mood",
        "type": "single_choice",
        "required": True,
        "options": [{"value": "good", "label": "Good"}, {"value": "bad", "label": "Bad"}],
    },
    {"key": "consent", "label": "Consent", "type": "boolean", "required": True},
]
ANSWERS = {"symptoms": "  headache  ", "mood": "good", "consent": True}


def _ok(response, what):
    assert response.status_code in (200, 201), f"{what}: {response.status_code} {response.text}"
    return response.json()


def _salon(client, make_user, headers_for, name):
    owner = make_user(
        "business_owner", full_name=f"{name} Owner", email=f"{name.lower()}.owner@example.com"
    )
    headers = headers_for(owner)
    business = _ok(
        client.post(
            "/businesses/", json={"name": f"{name} Salon", "business_type": "salon"}, headers=headers
        ),
        "business",
    )
    client_row = _ok(
        client.post(
            "/clients/",
            json={"full_name": f"{name} Guest", "contact_email": f"{name.lower()}.guest@example.com"},
            headers=headers,
        ),
        "client",
    )
    service = _ok(
        client.post(
            "/services/",
            json={
                "name": f"{name} Service",
                "price": 50,
                "duration_minutes": 60,
                "specialist_id": owner.id,
            },
            headers=headers,
        ),
        "service",
    )
    start = (datetime.utcnow() + timedelta(days=4)).replace(hour=10, minute=0, second=0, microsecond=0)
    visit = _ok(
        client.post(
            "/visits/",
            json={
                "client_id": client_row["id"],
                "specialist_id": owner.id,
                "service_id": service["id"],
                "date_start": start.isoformat(),
                "date_end": (start + timedelta(hours=1)).isoformat(),
                "price": 50,
            },
            headers=headers,
        ),
        "visit",
    )
    return {
        "owner": owner,
        "headers": headers,
        "business": business,
        "client": client_row,
        "service": service,
        "visit": visit,
    }


def _published_token(client, salon, name="Intake"):
    template = _ok(
        client.post(
            "/businesses/me/questionnaires/",
            json={"name": name, "fields": FIELDS},
            headers=salon["headers"],
        ),
        "template",
    )
    version = _ok(
        client.post(
            f"/businesses/me/questionnaires/{template['id']}/publish",
            json={"service_id": salon["service"]["id"]},
            headers=salon["headers"],
        ),
        "publish",
    )
    issued = _ok(
        client.post(
            f"/visits/{salon['visit']['id']}/questionnaire-token",
            headers=salon["headers"],
        ),
        "token",
    )
    return {"template": template, "version": version, "token": issued["token"], "expires_at": issued["expires_at"]}


def _guest(token):
    return {"X-Questionnaire-Token": token}


def test_guest_answers_are_replay_safe_and_owner_sees_them(client, make_user, headers_for):
    salon = _salon(client, make_user, headers_for, "Answer")
    issued = _published_token(client, salon)
    guest = _guest(issued["token"])
    visit_id = salon["visit"]["id"]

    form = client.get("/public/questionnaires", headers=guest)
    assert form.status_code == 200, form.text
    assert form.json()["version"] == 1
    assert form.json()["submitted_at"] is None
    assert [field["key"] for field in form.json()["fields"]] == ["symptoms", "mood", "consent"]

    partial = client.post(
        "/public/questionnaires/answers",
        json={"answers": {"mood": "good", "consent": True}},
        headers=guest,
    )
    assert partial.status_code == 422, partial.text
    assert partial.json()["error"]["detail"]["fields"]["symptoms"] == "required"
    assert client.get("/public/questionnaires", headers=guest).json()["submitted_at"] is None

    first = client.post("/public/questionnaires/answers", json={"answers": ANSWERS}, headers=guest)
    assert first.status_code == 200, first.text
    submitted_at = first.json()["submitted_at"]
    assert first.json()["version"] == 1

    replay = client.post("/public/questionnaires/answers", json={"answers": ANSWERS}, headers=guest)
    assert replay.status_code == 200, replay.text
    assert replay.json()["submitted_at"] == submitted_at

    changed = client.post(
        "/public/questionnaires/answers",
        json={"answers": {**ANSWERS, "mood": "bad"}},
        headers=guest,
    )
    assert changed.status_code == 409, changed.text
    assert client.get("/public/questionnaires", headers=guest).json()["submitted_at"] == submitted_at

    card = client.get(f"/visits/{visit_id}/questionnaire", headers=salon["headers"])
    assert card.status_code == 200, card.text
    assert card.json()["answers"] == {"symptoms": "headache", "mood": "good", "consent": True}
    assert card.json()["version"] == 1
    assert card.json()["submitted_at"] == submitted_at

    listed = client.get("/businesses/me/questionnaires/", headers=salon["headers"])
    assert listed.status_code == 200
    assert listed.json()[0]["published_version"] == 1


def test_expired_token_hides_questions_and_answers(client, make_user, headers_for, db):
    salon = _salon(client, make_user, headers_for, "Expiry")
    issued = _published_token(client, salon)
    guest = _guest(issued["token"])
    submitted = client.post("/public/questionnaires/answers", json={"answers": ANSWERS}, headers=guest)
    assert submitted.status_code == 200, submitted.text

    token = (
        db.query(QuestionnaireAccessToken)
        .filter(QuestionnaireAccessToken.visit_id == salon["visit"]["id"])
        .one()
    )
    assert token.token_hash != issued["token"]
    assert len(token.token_hash) == 64
    token.expires_at = utcnow() - timedelta(seconds=1)
    db.commit()

    assert client.get("/public/questionnaires", headers=guest).status_code == 404
    assert (
        client.post("/public/questionnaires/answers", json={"answers": ANSWERS}, headers=guest).status_code
        == 404
    )
    assert client.get("/public/questionnaires", headers=_guest("unknown-token")).status_code == 404
    # the owner's protected card is not tied to the guest link
    assert (
        client.get(f"/visits/{salon['visit']['id']}/questionnaire", headers=salon["headers"]).status_code
        == 200
    )


def test_foreign_tenant_cannot_read_or_issue_questionnaires(
    client, make_user, headers_for, db
):
    alpha = _salon(client, make_user, headers_for, "TenantAlpha")
    beta = _salon(client, make_user, headers_for, "TenantBeta")
    alpha_issued = _published_token(client, alpha)

    for path in (
        f"/visits/{alpha['visit']['id']}/questionnaire",
        f"/visits/{alpha['visit']['id']}/questionnaire/attachments",
    ):
        assert client.get(path, headers=beta["headers"]).status_code == 403, path
    assert (
        client.post(
            f"/visits/{alpha['visit']['id']}/questionnaire-token", headers=beta["headers"]
        ).status_code
        == 403
    )
    assert (
        client.get(
            f"/businesses/me/questionnaires/{alpha_issued['template']['id']}",
            headers=beta["headers"],
        ).status_code
        == 404
    )

    stored = (
        db.query(QuestionnaireAccessToken)
        .filter(QuestionnaireAccessToken.visit_id == alpha["visit"]["id"])
        .one()
    )
    stored.business_id = beta["business"]["id"]
    db.commit()
    assert (
        client.get("/public/questionnaires", headers=_guest(alpha_issued["token"])).status_code == 404
    )

    # even a token whose business still matches is unusable once it points at another tenant's visit
    second = _ok(
        client.post(
            f"/visits/{alpha['visit']['id']}/questionnaire-token", headers=alpha["headers"]
        ),
        "second token",
    )
    stored = (
        db.query(QuestionnaireAccessToken)
        .filter(QuestionnaireAccessToken.visit_id == alpha["visit"]["id"])
        .order_by(QuestionnaireAccessToken.id.desc())
        .first()
    )
    stored.visit_id = beta["visit"]["id"]
    db.commit()
    assert client.get("/public/questionnaires", headers=_guest(second["token"])).status_code == 404


def test_attachment_validation_and_storage_availability(
    client, make_user, headers_for, monkeypatch, db
):
    salon = _salon(client, make_user, headers_for, "Upload")
    issued = _published_token(client, salon)
    guest = _guest(issued["token"])

    bad_type = client.post(
        "/public/questionnaires/attachments",
        json={"filename": "scan.exe", "content_type": "application/x-msdownload", "size_bytes": 10},
        headers=guest,
    )
    assert bad_type.status_code == 422, bad_type.text

    too_big = client.post(
        "/public/questionnaires/attachments",
        json={
            "filename": "scan.pdf",
            "content_type": "application/pdf",
            "size_bytes": attachment_core.MAX_ATTACHMENT_BYTES + 1,
        },
        headers=guest,
    )
    assert too_big.status_code == 422, too_big.text

    monkeypatch.setattr(attachment_core.s3.settings, "aws_bucket", "")
    no_bucket = client.post(
        "/public/questionnaires/attachments",
        json={"filename": "scan.pdf", "content_type": "application/pdf", "size_bytes": 100},
        headers=guest,
    )
    assert no_bucket.status_code == 503, no_bucket.text

    monkeypatch.setattr(
        attachment_core,
        "presign_upload",
        lambda key, content_type: {
            "url": "https://storage.example.test/upload",
            "fields": {"key": key, "Content-Type": content_type},
        },
    )
    created = client.post(
        "/public/questionnaires/attachments",
        json={"filename": "../../private/scan.pdf", "content_type": "application/pdf", "size_bytes": 100},
        headers=guest,
    )
    assert created.status_code == 200, created.text
    attachment_id = created.json()["attachment_id"]
    assert created.json()["upload_url"] == "https://storage.example.test/upload"
    row = db.get(QuestionnaireAttachment, attachment_id)
    assert row.original_filename == "scan.pdf"
    assert row.business_id == salon["business"]["id"]
    assert row.visit_id == salon["visit"]["id"]

    complete = client.post(
        f"/public/questionnaires/attachments/{attachment_id}/complete", headers=guest
    )
    assert complete.status_code == 503, complete.text

    def missing(_key):
        raise attachment_core.AttachmentMissing

    monkeypatch.setattr(attachment_core, "head_uploaded", missing)
    assert (
        client.post(
            f"/public/questionnaires/attachments/{attachment_id}/complete", headers=guest
        ).status_code
        == 409
    )


def test_attachments_complete_list_limit_and_tenancy(
    client, make_user, headers_for, monkeypatch
):
    salon = _salon(client, make_user, headers_for, "Files")
    issued = _published_token(client, salon)
    guest = _guest(issued["token"])
    visit_id = salon["visit"]["id"]

    monkeypatch.setattr(
        attachment_core,
        "presign_upload",
        lambda key, content_type: {
            "url": "https://storage.example.test/upload",
            "fields": {"key": key, "Content-Type": content_type},
        },
    )
    attachment_ids = []
    for index in range(attachment_core.MAX_ATTACHMENTS_PER_VISIT):
        created = client.post(
            "/public/questionnaires/attachments",
            json={
                "filename": f"scan-{index}.pdf",
                "content_type": "application/pdf",
                "size_bytes": 100,
            },
            headers=guest,
        )
        assert created.status_code == 200, created.text
        attachment_ids.append(created.json()["attachment_id"])

    over_limit = client.post(
        "/public/questionnaires/attachments",
        json={"filename": "extra.pdf", "content_type": "application/pdf", "size_bytes": 100},
        headers=guest,
    )
    assert over_limit.status_code == 409, over_limit.text

    calls = {"count": 0}

    def head(_key):
        calls["count"] += 1
        return "application/pdf", 1234

    monkeypatch.setattr(attachment_core, "head_uploaded", head)
    first = client.post(
        f"/public/questionnaires/attachments/{attachment_ids[0]}/complete", headers=guest
    )
    assert first.status_code == 200, first.text
    assert first.json()["uploaded_at"] is not None
    assert first.json()["size_bytes"] == 1234
    repeated = client.post(
        f"/public/questionnaires/attachments/{attachment_ids[0]}/complete", headers=guest
    )
    assert repeated.status_code == 200
    assert calls["count"] == 1

    monkeypatch.setattr(attachment_core, "head_uploaded", lambda _key: ("image/png", 1234))
    assert (
        client.post(
            f"/public/questionnaires/attachments/{attachment_ids[1]}/complete", headers=guest
        ).status_code
        == 422
    )

    def missing(_key):
        raise attachment_core.AttachmentMissing

    monkeypatch.setattr(attachment_core, "head_uploaded", missing)
    assert (
        client.post(
            f"/public/questionnaires/attachments/{attachment_ids[2]}/complete", headers=guest
        ).status_code
        == 409
    )

    monkeypatch.setattr(
        attachment_core, "presign_download", lambda key: f"https://download.example.test/{key}"
    )
    listed = client.get(f"/visits/{visit_id}/questionnaire/attachments", headers=salon["headers"])
    assert listed.status_code == 200, listed.text
    assert len(listed.json()) == 1
    assert listed.json()[0]["filename"] == "scan-0.pdf"
    assert listed.json()[0]["url"].startswith("https://download.example.test/questionnaires/")

    def unavailable(_key):
        raise attachment_core.UploadsUnavailable

    monkeypatch.setattr(attachment_core, "presign_download", unavailable)
    listed = client.get(f"/visits/{visit_id}/questionnaire/attachments", headers=salon["headers"])
    assert listed.status_code == 200
    assert listed.json()[0]["url"] is None

    other = _salon(client, make_user, headers_for, "FilesOther")
    assert (
        client.get(f"/visits/{visit_id}/questionnaire/attachments", headers=other["headers"]).status_code
        == 403
    )
    other_issued = _published_token(client, other)
    assert (
        client.post(
            f"/public/questionnaires/attachments/{attachment_ids[3]}/complete",
            headers=_guest(other_issued["token"]),
        ).status_code
        == 404
    )
