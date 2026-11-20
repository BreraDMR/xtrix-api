"""Import review: a history file repeats one client on several rows.

The owner approves the preview, so what runs must be exactly what the preview showed,
including rows that repeat a client created a few lines above in the same file.
"""
import pytest

from app.db.models.client import Client
from app.db.models.visit import Visit
from tests.tenant_world import build_tenant
from tests.test_imports import _confirm, _csv, _prepare


def _rows(client, headers, batch_id):
    listed = client.get(f"/businesses/me/imports/{batch_id}/rows", headers=headers)
    assert listed.status_code == 200, listed.text
    return {item["row_number"]: item for item in listed.json()["items"]}


def _history(phone):
    # one new client with two visits, written the way a previous CRM exports history
    return [
        ["Repeat Person", phone, "", "", "", "", "2026-02-01 10:00", "Cut", "", "700", "completed", "cash"],
        ["Repeat Person", phone, "", "", "", "", "2026-03-01 10:00", "Cut", "", "800", "completed", "card"],
        ["Other Person", phone[:-1] + "9", "", "", "", "", "2026-02-02 11:00", "Cut", "", "300", "completed", "cash"],
    ]


@pytest.mark.parametrize(
    "policy, actions, clients, visits",
    [
        ("skip", {2: "create", 3: "skip", 4: "create"}, 2, 2),
        ("update", {2: "create", 3: "update", 4: "create"}, 2, 3),
        ("create", {2: "create", 3: "create", 4: "create"}, 3, 3),
    ],
)
def test_repeated_client_in_one_file_runs_exactly_as_previewed(
    client, db, make_user, headers_for, policy, actions, clients, visits
):
    world = build_tenant(client, make_user, headers_for, "Hist", "+42070081")
    phone = "+420700815551"
    batch = _prepare(client, world["headers"], _csv(_history(phone)), policy=policy)

    preview_rows = _rows(client, world["headers"], batch["id"])
    assert {number: row["action"] for number, row in preview_rows.items()} == actions
    counts = {name: sum(1 for value in actions.values() if value == name) for name in ("create", "update", "skip")}
    assert (batch["create_count"], batch["update_count"], batch["skip_count"]) == (
        counts["create"], counts["update"], counts["skip"])

    confirmed = _confirm(client, world["headers"], batch)
    assert confirmed.status_code == 200, confirmed.text
    result = confirmed.json()["batch"]
    assert result["status"] == "completed", result
    assert (result["created_count"], result["updated_count"], result["skipped_count"]) == (
        counts["create"], counts["update"], counts["skip"])

    executed = _rows(client, world["headers"], batch["id"])
    assert {number: row["action"] for number, row in executed.items()} == actions

    db.expire_all()
    imported = db.query(Client).filter(Client.full_name.in_(["Repeat Person", "Other Person"])).all()
    assert len(imported) == clients
    assert db.query(Visit).filter(Visit.client_id.in_([item.id for item in imported])).count() == visits

