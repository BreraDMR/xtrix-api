"""Business journal: transactionality, redaction, tenant scope and one event per change."""
import csv
import io
import json
from datetime import datetime, timedelta, timezone

from app.api.core import audit_service
from app.db.models.audit_log import AuditLog
from app.db.models.business_audit_event import BusinessAuditEvent
from app.db.models.client import Client
from app.db.models.user import User
from tests.tenant_world import build_tenant


def _events(db, business_id, action=None):
    db.expire_all()
    query = db.query(BusinessAuditEvent).filter(
        BusinessAuditEvent.business_id == business_id
    )
    if action:
        query = query.filter(BusinessAuditEvent.action == action)
    return query.order_by(BusinessAuditEvent.id).all()


def _start(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def test_visit_create_move_cancel_are_one_redacted_event_and_retry_is_idempotent(
    client, db, make_user, headers_for
):
    world = build_tenant(client, make_user, headers_for, "Alpha", "+42070051")
    business_id = world["business"]["id"]
    visit = world["visit"]

    created = _events(db, business_id, "visit.created")
    assert len(created) == 1
    assert created[0].entity_id == visit["id"]
    assert created[0].actor_user_id == world["owner"].id
    assert created[0].changes["status"] == "planned"
    assert created[0].changes["fields"]
    serialized = json.dumps(created[0].changes)
    assert "Alpha visit note" not in serialized
    assert "Alpha Klientka" not in serialized
    assert "contact" not in serialized.lower()

    start = _start(visit["date_start"]) + timedelta(hours=2)
    move = {
        "date_start": start.isoformat(),
        "date_end": (start + timedelta(hours=1)).isoformat(),
    }
    headers = {**world["headers"], "X-Request-ID": "move-request-1"}
    moved_response = client.put(
        f"/visits/{visit['id']}", json=move, headers=headers
    )
    assert moved_response.status_code == 200, moved_response.text
    replay = client.put(f"/visits/{visit['id']}", json=move, headers=headers)
    assert replay.status_code == 200, replay.text

    moved = _events(db, business_id, "visit.moved")
    assert len(moved) == 1
    assert moved[0].request_id == "move-request-1"
    assert "date_start" in moved[0].changes["fields"]
    assert moved[0].changes["date_start"]["new"].endswith("Z")

    cancelled = client.put(
        f"/visits/{visit['id']}", json={"status": "cancelled"}, headers=world["headers"]
    )
    assert cancelled.status_code == 200, cancelled.text
    events = _events(db, business_id)
    actions = [event.action for event in events]
    assert actions.count("visit.created") == 1
    assert actions.count("visit.moved") == 1
    assert actions.count("visit.cancelled") == 1
    assert "Alpha visit note" not in json.dumps(
        [event.changes for event in events], ensure_ascii=False
    )


def test_writer_joins_the_action_transaction_and_survives_a_journal_failure(
    client, db, make_user, headers_for, monkeypatch
):
    world = build_tenant(client, make_user, headers_for, "Alpha", "+42070052")
    business_id = world["business"]["id"]

    event = audit_service.record_event(
        db,
        action="visit.created",
        entity_type="visit",
        entity_id=987654,
        business_id=business_id,
        actor=world["owner"],
        changes={"fields": ["status"], "status": "planned"},
    )
    assert event is not None and event.id
    assert db.query(BusinessAuditEvent).filter(
        BusinessAuditEvent.entity_id == 987654
    ).count() == 1

    # No internal commit: the action rollback takes the journal row with it.
    db.rollback()
    assert db.query(BusinessAuditEvent).filter(
        BusinessAuditEvent.entity_id == 987654
    ).count() == 0

    class BrokenEvent:
        def __init__(self, **kwargs):
            raise RuntimeError("journal unavailable")

    monkeypatch.setattr(audit_service, "BusinessAuditEvent", BrokenEvent)
    row = Client(full_name="Journal Survives", owner_id=world["owner"].id)
    db.add(row)
    db.flush()
    assert audit_service.record_event(
        db,
        action="client.updated",
        entity_type="client",
        entity_id=row.id,
        business_id=business_id,
        changes={"fields": ["full_name"]},
    ) is None
    db.commit()
    assert db.get(Client, row.id) is not None
    assert db.query(BusinessAuditEvent).filter(
        BusinessAuditEvent.entity_id == row.id
    ).count() == 0


def test_owner_only_journal_is_scoped_and_old_platform_rows_stay_hidden(
    client, db, make_user, headers_for
):
    alpha = build_tenant(client, make_user, headers_for, "Alpha", "+42070053")
    beta = build_tenant(client, make_user, headers_for, "Beta", "+42070054")

    db.add(
        AuditLog(
            entity_type="client",
            entity_id=alpha["client"]["id"],
            action="legacy.platform",
            user_id=alpha["owner"].id,
            user_name="Alpha Owner",
            description="legacy-platform-marker",
        )
    )
    db.commit()

    response = client.get("/businesses/me/audit", headers=alpha["headers"])
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] >= 1
    assert "legacy-platform-marker" not in response.text
    assert all(item["action"] != "legacy.platform" for item in body["items"])
    assert all("Alpha" in (item["actor_name"] or "") or item["actor_role"] == "guest" for item in body["items"])

    other = client.get("/businesses/me/audit", headers=beta["headers"])
    assert other.status_code == 200, other.text
    assert "Alpha Owner" not in other.text

    staff = db.get(User, alpha["employee_user_id"])
    assert client.get("/businesses/me/audit", headers=headers_for(staff)).status_code == 403

    # Filtering by a foreign actor id stays inside the caller's tenant.
    foreign = client.get(
        "/businesses/me/audit",
        params={"actor_user_id": beta["owner"].id},
        headers=alpha["headers"],
    )
    assert foreign.status_code == 200, foreign.text
    assert foreign.json()["items"] == []

    solo = make_user("specialist", full_name="Solo Journal")
    solo_headers = headers_for(solo)
    solo_client = client.post(
        "/clients/", json={"full_name": "Solo Client"}, headers=solo_headers
    )
    assert solo_client.status_code == 200, solo_client.text
    updated = client.put(
        f"/clients/{solo_client.json()['id']}",
        json={"full_name": "Solo Client Renamed"},
        headers=solo_headers,
    )
    assert updated.status_code == 200, updated.text
    solo_journal = client.get("/businesses/me/audit", headers=solo_headers)
    assert solo_journal.status_code == 200, solo_journal.text
    assert [item["action"] for item in solo_journal.json()["items"]] == ["client.updated"]
    assert solo_journal.json()["items"][0]["actor_name"] == "Solo Journal"


def test_filters_pagination_and_csv_are_tenant_scoped_and_formula_safe(
    client, db, make_user, headers_for
):
    alpha = build_tenant(client, make_user, headers_for, "Alpha", "+42070056")
    beta = build_tenant(client, make_user, headers_for, "Beta", "+42070057")
    business_id = alpha["business"]["id"]

    csv_secret = "csv-secret@example.com"
    assert client.put(
        f"/clients/{alpha['client']['id']}",
        json={"contact_email": csv_secret},
        headers=alpha["headers"],
    ).status_code == 200

    db.add(
        BusinessAuditEvent(
            business_id=business_id,
            actor_user_id=alpha["owner"].id,
            actor_name='=HYPERLINK("http://evil.test")',
            actor_role="business_owner",
            action="visit.updated",
            entity_type="visit",
            entity_id=alpha["visit"]["id"],
            request_id="csv-formula-1",
            changes={"fields": ["status"], "status": {"old": "planned", "new": "completed"}},
        )
    )
    db.commit()

    first_page = client.get(
        "/businesses/me/audit", params={"page": 1, "per_page": 1}, headers=alpha["headers"]
    )
    assert first_page.status_code == 200, first_page.text
    body = first_page.json()
    assert body["total"] >= 3
    assert body["pages"] >= 3
    assert len(body["items"]) == 1
    second_page = client.get(
        "/businesses/me/audit", params={"page": 2, "per_page": 1}, headers=alpha["headers"]
    )
    assert second_page.json()["items"][0]["id"] != body["items"][0]["id"]

    filtered = client.get(
        "/businesses/me/audit",
        params={"action": "visit.updated", "entity_type": "visit", "actor_user_id": alpha["owner"].id},
        headers=alpha["headers"],
    )
    assert filtered.status_code == 200, filtered.text
    assert filtered.json()["total"] == 1
    assert filtered.json()["items"][0]["request_id"] == "csv-formula-1"

    tomorrow = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    empty = client.get(
        "/businesses/me/audit", params={"date_from": tomorrow}, headers=alpha["headers"]
    )
    assert empty.status_code == 200, empty.text
    assert empty.json()["items"] == []

    export = client.get("/businesses/me/audit/export/csv", headers=alpha["headers"])
    assert export.status_code == 200, export.text
    rows = list(csv.reader(io.StringIO(export.text)))
    assert any(cell.startswith("'=") for row in rows for cell in row)
    assert "Alpha Owner" in export.text
    assert csv_secret not in export.text
    assert "Alpha Klientka" not in export.text
    assert "Alpha visit note" not in export.text

    foreign_export = client.get("/businesses/me/audit/export/csv", headers=beta["headers"])
    assert foreign_export.status_code == 200
    assert "Alpha Owner" not in foreign_export.text
    assert csv_secret not in foreign_export.text

    staff = db.get(User, alpha["employee_user_id"])
    assert client.get(
        "/businesses/me/audit/export/csv", headers=headers_for(staff)
    ).status_code == 403


def test_redaction_keeps_only_catalogued_fields_and_never_values_for_client_cards(
    client, db, make_user, headers_for
):
    world = build_tenant(client, make_user, headers_for, "Alpha", "+42070055")
    secret_email = "journal-secret@example.com"
    updated = client.put(
        f"/clients/{world['client']['id']}",
        json={"full_name": "Alpha Renamed", "contact_email": secret_email},
        headers={**world["headers"], "X-Request-ID": "client-update-1"},
    )
    assert updated.status_code == 200, updated.text

    events = _events(db, world["business"]["id"], "client.updated")
    assert len(events) == 1
    assert events[0].request_id == "client-update-1"
    assert events[0].changes == {"fields": ["full_name", "contact_email"]}
    assert secret_email not in json.dumps(events[0].changes)

    sanitized = audit_service.sanitize_changes(
        "visit.updated",
        {
            "fields": ["note", "status", "answer", "unknown"],
            "note": "medical note that must not be stored",
            "status": {"old": "planned", "new": "completed"},
            "answer": "yes",
            "raw_context": "assistant context",
            "token": "raw-token",
            "allergies": "penicillin",
        },
    )
    assert sanitized == {
        "fields": ["note", "status"],
        "status": {"old": "planned", "new": "completed"},
    }
    dumped = json.dumps(sanitized)
    for secret in (
        "medical note",
        "assistant context",
        "raw-token",
        "penicillin",
        '"yes"',
    ):
        assert secret not in dumped

    assert audit_service.sanitize_changes(
        "client.updated",
        {
            "fields": ["full_name", "contact_email"],
            "full_name": "Secret Name",
            "contact_email": secret_email,
        },
    ) == {"fields": ["full_name", "contact_email"]}

    response = client.get(
        "/businesses/me/audit", params={"action": "client.updated"}, headers=world["headers"]
    )
    assert response.status_code == 200, response.text
    assert secret_email not in response.text
    assert "Journal Survives" not in response.text
