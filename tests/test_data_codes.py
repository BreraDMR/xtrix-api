"""Stored data carries codes, not Russian text - the frontend translates them."""
import re
from datetime import datetime

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import text

from app.db.session import engine
from tests.tenant_world import build_tenant

CYRILLIC = re.compile("[А-Яа-яЁё]")
SYSTEM_CODES = {"rent", "salaries", "stock_purchase", "marketing", "utilities", "subscriptions", "other"}


def test_new_product_unit_defaults_to_a_code(client, make_user, headers_for):
    a = build_tenant(client, make_user, headers_for, "Alpha", "+42070011")
    r = client.post("/products/", json={"name": "Wax", "business_id": a["business"]["id"]}, headers=a["headers"])
    assert r.status_code == 200, r.text
    assert r.json()["unit"] == "pcs"


def test_system_expense_categories_have_codes(client, make_user, headers_for):
    a = build_tenant(client, make_user, headers_for, "Alpha", "+42070011")
    cats = client.get("/expenses/categories", headers=a["headers"]).json()
    system = [c for c in cats if c["is_system"]]
    assert {c["code"] for c in system} == SYSTEM_CODES
    assert not any(CYRILLIC.search(c["name"]) for c in system), system
    own = next(c for c in cats if c["id"] == a["category"]["id"])
    assert own["code"] is None


def test_salary_payment_expense_is_a_code_not_a_sentence(client, make_user, headers_for):
    a = build_tenant(client, make_user, headers_for, "Alpha", "+42070011")
    expenses = client.get("/expenses/", headers=a["headers"]).json()["items"]
    auto = [e for e in expenses if e["source"] == "salary_payment"]
    assert len(auto) == 1, expenses
    exp = auto[0]
    assert exp["employee_id"] == a["employee"]["id"]
    assert exp["description"] == "Alpha payment"
    assert exp["category"]["code"] == "salaries"
    manual = next(e for e in expenses if e["id"] == a["expense"]["id"])
    assert manual["source"] is None and manual["employee_id"] is None

    report = client.get("/expenses/reports/expenses-by-category", headers=a["headers"]).json()
    assert "salaries" in {row["category_code"] for row in report}


def test_visit_write_off_is_marked_by_source(client, make_user, headers_for):
    from datetime import timedelta

    a = build_tenant(client, make_user, headers_for, "Alpha", "+42070011")
    start = (datetime.utcnow() + timedelta(days=5)).replace(hour=12, minute=0, second=0, microsecond=0)
    visit = client.post("/visits/", json={
        "client_id": a["client"]["id"], "specialist_id": a["owner"].id, "service_id": a["service"]["id"],
        "date_start": start.isoformat(), "date_end": (start + timedelta(hours=1)).isoformat(),
        "used_products": [{"product_id": a["product"]["id"], "quantity": 1}],
    }, headers=a["headers"]).json()
    usages = client.get("/products/usage", params={"visit_id": visit["id"]}, headers=a["headers"]).json()["items"]
    assert [(u["source"], u["notes"]) for u in usages] == [("visit", None)]
    manual = client.get("/products/usage", params={"product_id": a["product"]["id"]}, headers=a["headers"]).json()["items"]
    assert any(u["source"] is None and u["notes"] == "Alpha usage" for u in manual)


def test_seeded_plans_have_no_russian(client):
    with engine.connect() as conn:
        rows = conn.execute(text("SELECT code, name, features FROM subscription_plans")).all()
    assert rows
    assert not [r for r in rows if CYRILLIC.search(f"{r.name} {r.features}")], rows


def test_migration_turns_old_russian_rows_into_codes(client, make_user, headers_for):
    a = build_tenant(client, make_user, headers_for, "Alpha", "+42070011")
    cfg = Config("alembic.ini")
    before = ScriptDirectory.from_config(cfg).get_revision("0040_data_codes").down_revision
    command.downgrade(cfg, before)
    try:
        with engine.begin() as conn:
            conn.execute(text("UPDATE products SET unit = 'шт'"))
            emp = a["employee"]["id"]
            conn.execute(text("UPDATE expenses SET description = :d WHERE description = 'Alpha payment'"),
                          {"d": f"Выплата зарплаты сотруднику (ID: {emp}) - Alpha payment"})
            conn.execute(text("UPDATE expenses SET description = :d WHERE id = :id"),
                          {"d": "Выплата зарплаты сотруднику (ID: 999999)", "id": a["expense"]["id"]})
            conn.execute(text("UPDATE product_usages SET notes = 'Автоматическое списание при визите #7'"))
            names = {r.name for r in conn.execute(text("SELECT name FROM expense_categories WHERE is_system"))}
            assert "Аренда" in names  # downgrade put the old names back
    finally:
        command.upgrade(cfg, "head")

    with engine.connect() as conn:
        assert conn.execute(text("SELECT DISTINCT unit FROM products")).scalars().all() == ["pcs"]
        rows = conn.execute(text("SELECT id, source, employee_id, description FROM expenses ORDER BY id")).all()
        by_id = {r.id: r for r in rows}
        assert (by_id[a["expense"]["id"]].source, by_id[a["expense"]["id"]].employee_id,
                by_id[a["expense"]["id"]].description) == ("salary_payment", None, None)
        paid = [r for r in rows if r.description == "Alpha payment"]
        assert [(r.source, r.employee_id) for r in paid] == [("salary_payment", a["employee"]["id"])]
        usages = conn.execute(text("SELECT source, notes FROM product_usages")).all()
        assert usages and all((u.source, u.notes) == ("visit", None) for u in usages)
        codes = set(conn.execute(text("SELECT code FROM expense_categories WHERE is_system")).scalars())
        assert codes == SYSTEM_CODES
