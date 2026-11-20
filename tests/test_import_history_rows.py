"""Import review: a history file repeats one client on several rows, and prices keep their unit.

The owner approves the preview, so what runs must be exactly what the preview showed,
including rows that repeat a client created a few lines above in the same file.
"""
import pytest

from app.api.core import legacy_visit_currency
from app.db.models.business import Business
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


def test_imported_visit_prices_keep_the_business_currency(client, db, make_user, headers_for):
    world = build_tenant(client, make_user, headers_for, "Cur", "+42070082")
    business_id = world["business"]["id"]
    db.query(Business).filter(Business.id == business_id).update({"currency": "CZK"})
    db.commit()
    unknown_before = legacy_visit_currency.preview(db, business_id)["unknown_visits"]

    rows = [["Priced Person", "+420700825551", "", "", "", "", "2026-02-01 10:00", "Cut", "", "1200", "completed", "cash"]]
    batch = _prepare(client, world["headers"], _csv(rows))
    assert batch["currency"] == "CZK"
    confirmed = _confirm(client, world["headers"], batch)
    assert confirmed.status_code == 200, confirmed.text

    db.expire_all()
    person = db.query(Client).filter(Client.full_name == "Priced Person").one()
    visit = db.query(Visit).filter(Visit.client_id == person.id).one()
    assert visit.price == 1200
    # the preview told the owner these prices are CZK, so the visit must say so too
    assert visit.currency_snapshot == "CZK"
    assert legacy_visit_currency.preview(db, business_id)["unknown_visits"] == unknown_before


def test_csv_with_a_huge_cell_is_a_clear_file_error_not_a_crash(client, db, make_user, headers_for):
    # Python's csv module refuses fields over 128 KB; that must reach the owner as "malformed".
    world = build_tenant(client, make_user, headers_for, "Big", "+42070083")
    content = b'Name,Phone\n"' + b"a" * 200_000 + b'",+420700835551\n'
    response = client.post(
        "/businesses/me/imports",
        headers=world["headers"],
        files={"file": ("big.csv", content, "text/csv")},
    )
    assert response.status_code == 422, response.text
    assert "import_malformed_file" in response.text
