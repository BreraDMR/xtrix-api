"""Import wizard: safe parsing, dry-run, duplicate policy, idempotent execution and scope."""
import csv
import io
from datetime import datetime

import pytest
from openpyxl import Workbook

from app.api.core import import_service
from app.api.core.import_service import ImportFileError, parse_file, safe_filename
from app.db.models.business import Business
from app.db.models.client import Client
from app.db.models.client_business_relation import ClientBusinessRelation
from app.db.models.client_specialist_relation import ClientSpecialistRelation
from app.db.models.import_batch import ImportBatch, ImportRow
from app.db.models.notification_log import NotificationLog
from app.db.models.salary_calculation import SalaryCalculation
from app.db.models.service import Service
from app.db.models.service_payment import ServicePaymentAttempt
from app.db.models.transaction import Transaction
from app.db.models.user import User
from app.db.models.visit import Visit
from tests.tenant_world import build_tenant

HEADERS = [
    "Name",
    "Phone",
    "Email",
    "Birth date",
    "Gender",
    "Language",
    "Visit date",
    "Service",
    "Specialist",
    "Price",
    "Visit status",
    "Payment method",
]


def _csv(rows, headers=None):
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(headers or HEADERS)
    for row in rows:
        writer.writerow(row)
    return output.getvalue().encode("utf-8")


def _upload(client, headers, content, filename="clients.csv"):
    return client.post(
        "/businesses/me/imports",
        headers=headers,
        files={"file": (filename, content, "text/csv")},
    )


def _mapping_of(body):
    mapping = body["suggested_mapping"]
    assert "full_name" in mapping
    return mapping


def _prepare(client, headers, content, policy="skip", filename="clients.csv", batch=None):
    if batch is None:
        uploaded = _upload(client, headers, content, filename=filename)
        assert uploaded.status_code == 200, uploaded.text
        body = uploaded.json()
    else:
        body = batch
    mapped = client.put(
        f"/businesses/me/imports/{body['id']}/mapping",
        json={"mapping": _mapping_of(body)},
        headers=headers,
    )
    assert mapped.status_code == 200, mapped.text
    previewed = client.post(
        f"/businesses/me/imports/{body['id']}/preview",
        json={"duplicate_policy": policy},
        headers=headers,
    )
    assert previewed.status_code == 200, previewed.text
    return previewed.json()


def _confirm(client, headers, batch, policy=None):
    return client.post(
        f"/businesses/me/imports/{batch['id']}/confirm",
        json={
            "source_hash": batch["source_hash"],
            "mapping_version": batch["mapping_version"],
            "preview_version": batch["preview_version"],
            "duplicate_policy": policy or batch["duplicate_policy"],
        },
        headers=headers,
    )


def _other_tenant(client, make_user, headers_for, name, phone):
    owner = make_user(
        "business_owner", full_name=f"{name} Owner", email=f"{name.lower()}.owner@example.com"
    )
    headers = headers_for(owner)
    business = client.post(
        "/businesses/", json={"name": f"{name} Salon", "business_type": "salon"}, headers=headers
    )
    assert business.status_code == 200, business.text
    client_row = client.post(
        "/clients/",
        json={"full_name": f"{name} Client", "contact_phone": phone},
        headers=headers,
    )
    assert client_row.status_code == 200, client_row.text
    return {"owner": owner, "headers": headers, "client": client_row.json()}


def _count_rows(db, model, **filters):
    db.expire_all()
    query = db.query(model)
    for key, value in filters.items():
        query = query.filter(getattr(model, key) == value)
    return query.count()


def test_upload_mapping_and_preview_never_write_crm_and_reject_repeat(client, db, make_user, headers_for):
    world = build_tenant(client, make_user, headers_for, "Alpha", "+42070061")
    business_id = world["business"]["id"]
    existing_phone = world["client"]["contact_phone"]
    rows = [
        ["New Person", "+42070061111", "new@import.test", "", "", "", "", "", "", "", "", ""],
        [
            "Import Duplicate",
            existing_phone,
            world["client"]["contact_email"],
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
        ],
        ["Alpha Klientka", "+42070061112", "same.name@import.test", "", "", "", "", "", "", "", "", ""],
    ]
    clients_before = _count_rows(db, Client)
    relations_before = _count_rows(db, ClientBusinessRelation, business_id=business_id)

    uploaded = _upload(client, world["headers"], _csv(rows))
    assert uploaded.status_code == 200, uploaded.text
    batch = uploaded.json()
    assert batch["status"] == "uploaded"
    assert batch["row_count"] == 3
    assert batch["mapping_version"] == 0
    assert batch["mapping"]["full_name"] == "Name" if batch["mapping"] else True
    assert batch["suggested_mapping"]["phone"] == "Phone"

    preview = _prepare(client, world["headers"], _csv(rows), batch=batch)
    assert preview["status"] == "previewed"
    assert preview["valid_count"] == 3
    assert preview["error_count"] == 0
    assert preview["create_count"] == 2
    assert preview["skip_count"] == 1
    assert preview["mapping_version"] == 1
    assert preview["preview_version"] == 1

    assert _count_rows(db, Client) == clients_before
    assert _count_rows(db, ClientBusinessRelation, business_id=business_id) == relations_before

    listed = client.get(
        f"/businesses/me/imports/{batch['id']}/rows", headers=world["headers"]
    )
    assert listed.status_code == 200, listed.text
    items = listed.json()["items"]
    assert [item["row_number"] for item in items] == [2, 3, 4]
    actions = {item["row_number"]: item["action"] for item in items}
    assert actions[2] == "create"
    assert actions[3] == "skip"
    assert actions[4] == "create"

    repeat = _upload(client, world["headers"], _csv(rows))
    assert repeat.status_code == 409, repeat.text
    assert repeat.json()["error"]["detail"]["existing_batch_id"] == batch["id"]

    other = _other_tenant(client, make_user, headers_for, "Beta", "+42070062100")
    allowed = _upload(client, other["headers"], _csv(rows))
    assert allowed.status_code == 200, allowed.text
    foreign_read = client.get(
        f"/businesses/me/imports/{batch['id']}", headers=other["headers"]
    )
    assert foreign_read.status_code == 404


def test_confirm_imports_clients_and_visits_without_payments_salary_or_notifications(
    client, db, make_user, headers_for
):
    world = build_tenant(client, make_user, headers_for, "Alpha", "+42070062")
    business_id = world["business"]["id"]
    db.query(Business).filter(Business.id == business_id).update({"timezone": "Europe/Prague"})
    db.commit()
    rows = [
        [
            "Imported Person",
            "+42070062222",
            "imported@example.com",
            "1990-05-06",
            "female",
            "cz",
            "2026-01-15 10:00",
            "Balayage",
            "Alpha Owner",
            "1500",
            "completed",
            "cash",
        ],
        [
            "Updated Klientka",
            world["client"]["contact_phone"],
            world["client"]["contact_email"],
            "",
            "",
            "",
            "2026-01-16 12:00",
            "Alpha Massage",
            "Alpha Owner",
            "500",
            "completed",
            "cash",
        ],
    ]
    batch = _prepare(client, world["headers"], _csv(rows), policy="update")
    assert batch["valid_count"] == 2
    assert batch["create_count"] == 1
    assert batch["update_count"] == 1

    side_effect_models = (ServicePaymentAttempt, SalaryCalculation, NotificationLog, Transaction)
    before = {model.__name__: _count_rows(db, model) for model in side_effect_models}
    visits_before = _count_rows(db, Visit)

    confirmed = _confirm(client, world["headers"], batch)
    assert confirmed.status_code == 200, confirmed.text
    summary = confirmed.json()
    assert summary["already_completed"] is False
    result = summary["batch"]
    assert result["status"] == "completed", result
    assert result["created_count"] == 1
    assert result["updated_count"] == 1
    assert result["failed_count"] == 0
    assert result["error_count"] == 0
    assert result["source_expires_at"] is None

    stored = db.query(ImportBatch).filter(ImportBatch.id == batch["id"]).one()
    assert stored.source_blob is None

    new_client = (
        db.query(Client)
        .filter(Client.contact_phone == "+42070062222")
        .one()
    )
    assert db.query(ClientBusinessRelation).filter(
        ClientBusinessRelation.client_id == new_client.id,
        ClientBusinessRelation.business_id == business_id,
    ).count() == 1
    assert db.query(ClientSpecialistRelation).filter(
        ClientSpecialistRelation.client_id == new_client.id,
        ClientSpecialistRelation.business_id == business_id,
    ).count() == 1

    existing = db.get(Client, world["client"]["id"])
    db.refresh(existing)
    assert existing.full_name == "Updated Klientka"

    new_visits = db.query(Visit).filter(Visit.client_id == new_client.id).all()
    assert len(new_visits) == 1
    assert new_visits[0].date_start == datetime(2026, 1, 15, 9, 0)
    assert new_visits[0].date_end == datetime(2026, 1, 15, 10, 0)
    assert new_visits[0].price == 1500
    assert new_visits[0].business_id == business_id
    assert new_visits[0].status == "completed"
    service = db.query(Service).filter(Service.name == "Balayage").one()
    assert service.business_id == business_id
    assert service.duration_minutes == 60

    assert _count_rows(db, Visit) == visits_before + 2
    for model in side_effect_models:
        assert _count_rows(db, model) == before[model.__name__], model.__name__

    replay = _confirm(client, world["headers"], result)
    assert replay.status_code == 200, replay.text
    assert replay.json()["already_completed"] is True
    assert _count_rows(db, Visit) == visits_before + 2
    assert db.query(Client).filter(Client.contact_phone == "+42070062222").count() == 1

    report = client.get(
        f"/businesses/me/imports/{batch['id']}/report.csv", headers=world["headers"]
    )
    assert report.status_code == 200, report.text
    assert report.headers["content-type"].startswith("text/csv")
    report_rows = list(csv.DictReader(io.StringIO(report.text)))
    assert report_rows[-1]["Status"] == "summary"
    assert "created=1" in report_rows[-1]["Error detail"]
    assert all(row["Status"] != "summary" for row in report_rows[:-1])
    phones = [row["Phone"] for row in report_rows if row["Phone"]]
    assert phones and all(phone.startswith("'") for phone in phones)


def test_duplicate_policies_skip_update_create_and_other_business_contacts_stay_private(
    client, db, make_user, headers_for
):
    world = build_tenant(client, make_user, headers_for, "Alpha", "+42070063")
    phone = world["client"]["contact_phone"]
    other = _other_tenant(client, make_user, headers_for, "Beta", "+42070063100")
    foreign_phone = other["client"]["contact_phone"]
    names = ["Should Stay", "Renamed By Import", "Second Copy"]

    def upload_one(name, policy, comment):
        content = _csv([[name, phone, comment]], headers=["Name", "Phone", "Comment"])
        return _prepare(client, world["headers"], content, policy=policy)

    skip_batch = upload_one(names[0], "skip", "skip-run")
    assert skip_batch["skip_count"] == 1
    skipped = _confirm(client, world["headers"], skip_batch)
    assert skipped.status_code == 200, skipped.text
    assert skipped.json()["batch"]["skipped_count"] == 1
    db.refresh(db.get(Client, world["client"]["id"]))
    assert db.get(Client, world["client"]["id"]).full_name == "Alpha Klientka"

    update_batch = upload_one(names[1], "update", "update-run")
    assert update_batch["update_count"] == 1
    updated = _confirm(client, world["headers"], update_batch)
    assert updated.status_code == 200, updated.text
    assert updated.json()["batch"]["updated_count"] == 1
    assert db.get(Client, world["client"]["id"]).full_name == names[1]

    create_batch = upload_one(names[2], "create", "create-run")
    assert create_batch["create_count"] == 1
    created = _confirm(client, world["headers"], create_batch)
    assert created.status_code == 200, created.text
    assert created.json()["batch"]["created_count"] == 1
    assert db.query(Client).filter(Client.contact_phone == phone).count() == 2

    foreign = _prepare(
        client,
        world["headers"],
        _csv([["Foreign Contact", foreign_phone, "", "", "", "", "", "", "", "", "", ""]]),
    )
    assert foreign["create_count"] == 1
    assert foreign["skip_count"] == 0
    assert "Beta" not in str(foreign)


def test_crash_leaves_checkpoint_and_retry_does_not_duplicate(client, db, make_user, headers_for, monkeypatch):
    world = build_tenant(client, make_user, headers_for, "Alpha", "+42070064")
    rows = [
        ["Retry One", "+42070064111", "", "", "", "", "", "", "", "", "", ""],
        ["Retry Two", "+42070064112", "", "", "", "", "", "", "", "", "", ""],
        ["Retry Three", "+42070064113", "", "", "", "", "", "", "", "", "", ""],
    ]
    batch = _prepare(client, world["headers"], _csv(rows))
    db.expire_all()
    owner = db.get(User, world["owner"].id)
    stored = db.query(ImportBatch).filter(ImportBatch.id == batch["id"]).one()
    scope = import_service.scope_for(db, owner)
    original = import_service._execute_row
    calls = {"n": 0}

    def flaky(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 2:
            raise KeyboardInterrupt
        return original(*args, **kwargs)

    monkeypatch.setattr(import_service, "_execute_row", flaky)
    with pytest.raises(KeyboardInterrupt):
        import_service.execute_batch(db, stored, scope, owner)
    db.rollback()
    stored = db.query(ImportBatch).filter(ImportBatch.id == batch["id"]).one()
    assert stored.status == "importing"
    statuses = {
        row.status
        for row in db.query(ImportRow).filter(ImportRow.batch_id == stored.id).all()
    }
    assert statuses == {"imported", "valid"}

    monkeypatch.setattr(import_service, "_execute_row", original)
    import_service.execute_batch(db, stored, scope, owner)
    db.expire_all()
    assert db.query(Client).filter(Client.contact_phone == "+42070064111").count() == 1
    assert db.query(Client).filter(Client.contact_phone == "+42070064112").count() == 1
    assert db.query(Client).filter(Client.contact_phone == "+42070064113").count() == 1
    stored = db.query(ImportBatch).filter(ImportBatch.id == batch["id"]).one()
    assert stored.status == "completed"
    assert stored.created_count == 3


def test_parser_rejects_unsafe_and_malformed_files():
    def code_for(content, filename, expected):
        with pytest.raises(ImportFileError) as error:
            parse_file(content, filename)
        assert error.value.code == expected, error.value.code

    code_for(b"\xff\xfe\x00bad", "clients.csv", "import_encoding")
    code_for(b"Name,Name\nA,B", "clients.csv", "import_duplicate_headers")
    code_for(b"", "clients.csv", "import_file_empty")
    code_for(b"Name\n" + b"A\n" * 5001, "clients.csv", "import_too_many_rows")
    headers = ",".join(f"Column {index}" for index in range(51))
    row = ",".join(f"value {index}" for index in range(51))
    code_for(f"{headers}\n{row}\n".encode(), "clients.csv", "import_too_many_columns")
    code_for(b"x" * (import_service.MAX_UPLOAD_BYTES + 1), "clients.csv", "import_file_too_large")
    code_for(b"not a zip", "clients.xlsx", "import_malformed_file")
    code_for(b"PK\x03\x04vbaProject.bin", "clients.xlsx", "import_macros_not_allowed")
    code_for(b"anything", "clients.xlsm", "import_macros_not_allowed")
    code_for(b"Name\nA", "clients.txt", "import_file_type")

    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["Name"])
    sheet.append(["=SUM(1+1)"])
    buffer = io.BytesIO()
    workbook.save(buffer)
    headers_out, rows_out, _filename = parse_file(buffer.getvalue(), "clients.xlsx")
    assert headers_out == ["Name"]
    assert rows_out[0].error_code == "formula_not_allowed"
    assert rows_out[0].values == [None]

    headers_out, rows_out, filename = parse_file(b"Name\n=Plain Text\n", "../../etc/clients.csv")
    assert filename == "clients.csv"
    assert headers_out == ["Name"]
    assert rows_out[0].values == ["=Plain Text"]
    assert rows_out[0].error_code is None
    assert safe_filename("..\\..\\windows\\evil.csv") == "evil.csv"


def test_mapping_preview_validation_and_row_errors(client, db, make_user, headers_for):
    world = build_tenant(client, make_user, headers_for, "Alpha", "+42070065")
    no_name = _upload(client, world["headers"], b"Phone,Email\n+42070065001,a@b.cz\n")
    assert no_name.status_code == 200, no_name.text
    batch = no_name.json()
    mapping = client.put(
        f"/businesses/me/imports/{batch['id']}/mapping",
        json={"mapping": {"phone": "Phone"}},
        headers=world["headers"],
    )
    assert mapping.status_code == 422, mapping.text
    unknown = client.put(
        f"/businesses/me/imports/{batch['id']}/mapping",
        json={"mapping": {"bogus": "Phone"}},
        headers=world["headers"],
    )
    assert unknown.status_code == 422, unknown.text

    rows = [
        ["", "+42070065001", "", "", "", "", "", "", "", "", "", ""],
        ["Bad Phone", "123", "", "", "", "", "", "", "", "", "", ""],
        ["Bad Email", "", "not-an-email", "", "", "", "", "", "", "", "", ""],
        ["Bad Date", "", "", "31.02.2000", "", "", "", "", "", "", "", ""],
        ["Bad Gender", "", "", "", "unknown", "", "", "", "", "", "", ""],
        ["Bad Visit", "", "", "", "", "", "not-a-date", "Service", "", "", "", ""],
        ["Bad Price", "", "", "", "", "", "2026-01-01", "Service", "", "abc", "", ""],
    ]
    preview = _prepare(client, world["headers"], _csv(rows))
    assert preview["valid_count"] == 0
    assert preview["error_count"] == len(rows)
    errors = client.get(
        f"/businesses/me/imports/{preview['id']}/rows?status=error",
        headers=world["headers"],
    )
    assert errors.status_code == 200, errors.text
    by_row = {item["row_number"]: item["error_code"] for item in errors.json()["items"]}
    assert by_row[2] == "required_field"
    assert by_row[3] == "invalid_phone"
    assert by_row[4] == "invalid_email"
    assert by_row[5] == "invalid_date"
    assert by_row[6] == "invalid_choice"
    assert by_row[7] == "invalid_date"
    assert by_row[8] == "invalid_price"

    bad_filter = client.get(
        f"/businesses/me/imports/{preview['id']}/rows?status=unknown",
        headers=world["headers"],
    )
    assert bad_filter.status_code == 422

    refused = _confirm(client, world["headers"], preview)
    assert refused.status_code == 200, refused.text
    assert refused.json()["batch"]["status"] == "completed_with_errors"
    assert refused.json()["batch"]["created_count"] == 0


def test_confirm_requires_the_previewed_file_mapping_and_policy(client, db, make_user, headers_for):
    world = build_tenant(client, make_user, headers_for, "Alpha", "+42070066")
    batch = _prepare(
        client,
        world["headers"],
        _csv([["Preview Guard", "+42070066111", "", "", "", "", "", "", "", "", "", ""]]),
    )
    outdated = _confirm(client, world["headers"], batch, policy="update")
    assert outdated.status_code == 409, outdated.text
    assert outdated.json()["error"]["detail"]["code"] == "import_preview_outdated"

    remap = client.put(
        f"/businesses/me/imports/{batch['id']}/mapping",
        json={"mapping": batch["mapping"]},
        headers=world["headers"],
    )
    assert remap.status_code == 200, remap.text
    assert remap.json()["preview_version"] == 0
    assert remap.json()["mapping_version"] == 2
    assert remap.json()["status"] == "mapped"
    stale_payload = dict(remap.json())
    stale_payload["preview_version"] = 1
    stale = _confirm(client, world["headers"], stale_payload)
    assert stale.status_code == 409, stale.text
    assert stale.json()["error"]["detail"]["code"] == "import_preview_outdated"

    previewed = client.post(
        f"/businesses/me/imports/{batch['id']}/preview",
        json={"duplicate_policy": "skip"},
        headers=world["headers"],
    )
    assert previewed.status_code == 200, previewed.text
    fresh = previewed.json()
    confirmed = _confirm(client, world["headers"], fresh)
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["batch"]["status"] == "completed"


def test_source_blob_expires_and_staff_or_foreign_batches_are_closed(client, db, make_user, headers_for):
    world = build_tenant(client, make_user, headers_for, "Alpha", "+42070067")
    batch = _prepare(
        client,
        world["headers"],
        _csv([["Expiring", "+42070067111", "", "", "", "", "", "", "", "", "", ""]]),
    )
    stored = db.query(ImportBatch).filter(ImportBatch.id == batch["id"]).one()
    assert stored.source_blob is not None
    stored.source_expires_at = datetime(2000, 1, 1)
    db.commit()
    read = client.get(f"/businesses/me/imports/{batch['id']}", headers=world["headers"])
    assert read.status_code == 200, read.text
    assert read.json()["source_expires_at"] is None
    db.expire_all()
    assert db.query(ImportBatch).filter(ImportBatch.id == batch["id"]).one().source_blob is None

    staff = db.get(User, world["employee_user_id"])
    staff_headers = headers_for(staff)
    assert client.get("/businesses/me/imports", headers=staff_headers).status_code == 403
    assert client.get(f"/businesses/me/imports/{batch['id']}", headers=staff_headers).status_code == 403

    admin = make_user("admin", full_name="Platform Admin")
    assert client.get("/businesses/me/imports", headers=headers_for(admin)).status_code == 403

    other = _other_tenant(client, make_user, headers_for, "Beta", "+42070067200")
    for path in (
        f"/businesses/me/imports/{batch['id']}",
        f"/businesses/me/imports/{batch['id']}/rows",
        f"/businesses/me/imports/{batch['id']}/report.csv",
    ):
        assert client.get(path, headers=other["headers"]).status_code == 404
    foreign_confirm = _confirm(client, other["headers"], batch)
    assert foreign_confirm.status_code == 404


def test_xlsx_rows_and_business_timezone_are_imported_deterministically(client, db, make_user, headers_for):
    world = build_tenant(client, make_user, headers_for, "Alpha", "+42070068")
    business_id = world["business"]["id"]
    db.query(Business).filter(Business.id == business_id).update({"timezone": "Europe/Prague"})
    db.commit()
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["Name", "Phone", "Visit date", "Service", "Price"])
    sheet.append(["XLSX Person", "+42070068111", datetime(2026, 5, 1, 9, 30), "XLSX Service", 700])
    buffer = io.BytesIO()
    workbook.save(buffer)
    uploaded = client.post(
        "/businesses/me/imports",
        headers=world["headers"],
        files={
            "file": (
                "clients.xlsx",
                buffer.getvalue(),
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        },
    )
    assert uploaded.status_code == 200, uploaded.text
    body = uploaded.json()
    mapped = client.put(
        f"/businesses/me/imports/{body['id']}/mapping",
        json={"mapping": body["suggested_mapping"]},
        headers=world["headers"],
    )
    assert mapped.status_code == 200, mapped.text
    previewed = client.post(
        f"/businesses/me/imports/{body['id']}/preview",
        json={"duplicate_policy": "skip"},
        headers=world["headers"],
    )
    assert previewed.status_code == 200, previewed.text
    batch = previewed.json()
    assert batch["timezone"] == "Europe/Prague"
    assert batch["currency"] == "EUR"
    confirmed = _confirm(client, world["headers"], batch)
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["batch"]["status"] == "completed"
    imported = db.query(Client).filter(Client.contact_phone == "+42070068111").one()
    visit = db.query(Visit).filter(Visit.client_id == imported.id).one()
    assert visit.date_start == datetime(2026, 5, 1, 7, 30)
    assert visit.date_end == datetime(2026, 5, 1, 8, 30)
    assert db.query(Service).filter(Service.name == "XLSX Service").count() == 1
