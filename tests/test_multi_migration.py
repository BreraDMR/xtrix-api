"""Backfill legacy visits without changing their original fields or money."""
from datetime import datetime, timedelta
from decimal import Decimal

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError


def test_multi_backfill_roundtrip_preserves_legacy_visits(db, make_user):
    owner_id = make_user().id
    db.rollback()
    bind = db.get_bind()
    config = Config("alembic.ini")
    start = datetime(2030, 5, 6, 8)

    def visits():
        with bind.connect() as connection:
            return [dict(row) for row in connection.execute(
                text("SELECT * FROM visits ORDER BY id")
            ).mappings()]

    try:
        command.downgrade(config, "0067_waitlist")
        assert "visit_items" not in inspect(bind).get_table_names()
        with bind.begin() as connection:
            client_id = connection.execute(text(
                "INSERT INTO clients (full_name, owner_id) VALUES ('Legacy chain', :owner) RETURNING id"
            ), {"owner": owner_id}).scalar_one()
            service_id = connection.execute(text(
                "INSERT INTO services (name, specialist_id, duration_minutes, price) "
                "VALUES ('Catalog changed', :owner, 45, 999) RETURNING id"
            ), {"owner": owner_id}).scalar_one()
            for n, end, price, service, status in [
                (0, start + timedelta(minutes=30), 12.345, service_id, "planned"),
                (1, None, None, service_id, "planned"),
                (2, start + timedelta(days=2, minutes=-10), 0, service_id, "cancelled"),
                (3, None, 27.10, None, "planned"),
            ]:
                connection.execute(text(
                    "INSERT INTO visits (client_id, specialist_id, service_id, service_name, "
                    "date_start, date_end, price, status, note) "
                    "VALUES (:client, :owner, :service, 'Purchased name', :start, :end, :price, :status, 'Keep me')"
                ), {"client": client_id, "owner": owner_id, "service": service,
                    "start": start + timedelta(days=n), "end": end, "price": price, "status": status})
        before = visits()
        first_backfill = None
        for _ in range(2):
            command.upgrade(config, "0068_visit_items")
            assert visits() == before
            with bind.connect() as connection:
                rows = [dict(row) for row in connection.execute(text(
                    "SELECT visit_id, position, service_id, specialist_id, service_name, price, "
                    "duration_minutes, buffer_before_minutes, buffer_after_minutes, date_start, date_end "
                    "FROM visit_items ORDER BY visit_id"
                )).mappings()]
            assert len(rows) == 4
            assert [row["duration_minutes"] for row in rows] == [30, 45, 45, 60]
            assert [row["price"] for row in rows] == [Decimal("12.35"), None, Decimal("0"), Decimal("27.10")]
            for row, old in zip(rows, before):
                assert row["visit_id"] == old["id"]
                assert row["position"] == 0
                assert row["service_id"] == old["service_id"]
                assert row["specialist_id"] == owner_id
                assert row["service_name"] == "Purchased name"
                assert row["date_start"] == old["date_start"]
                assert row["date_end"] == old["date_start"] + timedelta(minutes=row["duration_minutes"])
                assert row["buffer_before_minutes"] == row["buffer_after_minutes"] == 0
            if first_backfill is None:
                first_backfill = rows
            assert rows == first_backfill
            with pytest.raises(IntegrityError), bind.begin() as connection:
                connection.execute(text(
                    "INSERT INTO visit_items (visit_id, position, duration_minutes, date_start, date_end) "
                    "SELECT visit_id, position, duration_minutes, date_start, date_end FROM visit_items LIMIT 1"
                ))
            command.downgrade(config, "0067_waitlist")
            assert visits() == before
        command.upgrade(config, "0076_gift_order_terms")
        before_slots = visits()
        with bind.connect() as connection:
            old_items = [dict(row) for row in connection.execute(
                text("SELECT * FROM visit_items ORDER BY visit_id")
            ).mappings()]
        command.upgrade(config, "0077_visit_item_slots")
        assert [{key: row[key] for key in before_slots[0]} for row in visits()] == before_slots
        with bind.connect() as connection:
            new_items = [dict(row) for row in connection.execute(
                text("SELECT * FROM visit_items ORDER BY visit_id")
            ).mappings()]
        assert [{key: row[key] for key in old_items[0]} for row in new_items] == old_items
        assert [row["active"] for row in new_items] == [True, True, False, True]
        command.downgrade(config, "0076_gift_order_terms")
        assert visits() == before_slots
        with bind.connect() as connection:
            assert [dict(row) for row in connection.execute(
                text("SELECT * FROM visit_items ORDER BY visit_id")
            ).mappings()] == old_items
    finally:
        db.rollback()
        command.upgrade(config, "head")
