from tests import test_gifts
from alembic import command
from alembic.config import Config
from sqlalchemy import text

gift_world = test_gifts.gift_world


def test_upgrade_recovers_paid_terms_from_the_issued_certificate(client, db, gift_world):
    world = gift_world
    order, paid = test_gifts._pay(client, world, request_key="historical-paid-order")
    paid_id = test_gifts._order(db, paid).id
    pending = test_gifts._ok(test_gifts._purchase(client, world, request_key="historical-pending-order"), "pending")
    pending_id = test_gifts._order(db, pending).id
    offer_id = world["money_offer"]["id"]
    original_name = world["money_offer"]["name"]
    db.execute(text("UPDATE gift_offers SET name='Changed offer', face_value=9, validity_days=2 WHERE id=:id"), {"id": offer_id})
    # This migration fixture represents rows written before the 0086 payroll snapshots.
    # Keep its old amounts, but remove only this tenant's newer payroll metadata.
    db.execute(
        text("UPDATE expenses SET salary_payment_id = NULL WHERE salary_payment_id = :id"),
        {"id": world["payment"]["id"]},
    )
    db.execute(
        text("UPDATE salary_payments SET currency = NULL, amount_exact = NULL WHERE id = :id"),
        {"id": world["payment"]["id"]},
    )
    db.execute(
        text("UPDATE salary_adjustments SET currency = NULL, amount_exact = NULL WHERE id = :id"),
        {"id": world["adjustment"]["id"]},
    )
    db.execute(
        text("""UPDATE salary_calculations SET salary_period_id = NULL, currency = NULL,
            business_id_snapshot = NULL, total_revenue_exact = NULL, commission_amount_exact = NULL,
            base_salary_exact = NULL, total_salary_exact = NULL, adjustments_total_exact = NULL,
            total_with_adjustments_exact = NULL, paid_amount_exact = NULL WHERE id = :id"""),
        {"id": world["salary"]["id"]},
    )
    db.execute(
        text("DELETE FROM salary_periods WHERE employee_id = :id"),
        {"id": world["employee"]["id"]},
    )
    db.execute(
        text("UPDATE employees SET base_salary_currency = NULL, base_salary_exact = NULL WHERE id = :id"),
        {"id": world["employee"]["id"]},
    )
    db.commit()
    db.close()
    config = Config("alembic.ini")
    try:
        command.downgrade(config, "0075_google_login")
        command.upgrade(config, "head")
        terms = dict(db.execute(text("SELECT id, offer_snapshot FROM gift_orders")).all())
        assert terms[paid_id]["name"] == original_name
        assert float(terms[paid_id]["face_value"]) == 50
        assert terms[paid_id]["validity_days"] == 60
        # There was no snapshot for an unpaid legacy order; its former terms are unknowable.
        assert terms[pending_id]["name"] == "Changed offer"
        assert float(terms[pending_id]["face_value"]) == 9
    finally:
        db.rollback()
        command.upgrade(config, "head")
