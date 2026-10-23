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
