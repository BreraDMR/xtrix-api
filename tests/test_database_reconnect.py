"""A database that sleeps may close sockets while they sit in the pool."""
from sqlalchemy import text

from app.db.session import engine


def test_stale_pooled_connection_is_replaced_before_the_first_query():
    engine.dispose()
    try:
        with engine.connect() as connection:
            driver = connection.connection.driver_connection
            assert connection.execute(text("SELECT 1")).scalar_one() == 1
        # The server/proxy can drop this idle connection without notifying our pool.
        driver.close()
        with engine.connect() as connection:
            assert connection.execute(text("SELECT 1")).scalar_one() == 1
    finally:
        engine.dispose()
