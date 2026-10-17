import os

import pytest
from sqlalchemy import MetaData, insert, select, text
from sqlalchemy.engine import make_url

# Settings are read at import time, so the guard has to run before anything from app/ is imported.
_url = os.environ.get("DATABASE_URL", "")
_db_name = (make_url(_url).database or "") if _url else ""
if "test" not in _db_name:
    raise RuntimeError(
        f"Refusing to run tests against database {_db_name!r}: every test wipes the tables. "
        "Point DATABASE_URL at a database with 'test' in its name (scripts/check.sh does that)."
    )
os.environ.setdefault("JWT_SECRET", "test-secret-key-minimum-32-characters-long-for-testing")
os.environ.setdefault("ENVIRONMENT", "test")

from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.api.core.security import create_access_token  # noqa: E402
from app.db.session import SessionLocal, engine  # noqa: E402

_seed_rows: dict[str, list[dict]] = {}
_metadata = MetaData()


@pytest.fixture(scope="session", autouse=True)
def _migrated_db():
    # run the real migrations, so the tests also prove the migration chain still works
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
    command.upgrade(Config("alembic.ini"), "head")
    _metadata.reflect(bind=engine)
    # some migrations seed reference data (plans, categories...) - remember it to restore after each test
    with engine.connect() as conn:
        for table in _metadata.sorted_tables:
            if table.name == "alembic_version":
                continue
            rows = [dict(r) for r in conn.execute(select(table)).mappings()]
            if rows:
                _seed_rows[table.name] = rows
    yield


@pytest.fixture(autouse=True)
def _clean_tables():
    yield
    tables = [t for t in _metadata.sorted_tables if t.name != "alembic_version"]
    with engine.begin() as conn:
        names = ", ".join(f'"{t.name}"' for t in tables)
        conn.execute(text(f"TRUNCATE {names} RESTART IDENTITY CASCADE"))
        for table in tables:
            rows = _seed_rows.get(table.name)
            if not rows:
                continue
            conn.execute(insert(table), rows)
            if "id" in table.c:
                conn.execute(text(
                    f"SELECT setval(pg_get_serial_sequence('\"{table.name}\"', 'id'), "
                    f"(SELECT COALESCE(MAX(id), 1) FROM \"{table.name}\"))"
                ))


@pytest.fixture
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def client():
    from app.main import app

    return TestClient(app)


@pytest.fixture
def make_user(db):
    from app.api.crud import crud_user
    from app.schemas.user import UserCreate

    counter = {"n": 0}

    def _make(role="specialist", **overrides):
        counter["n"] += 1
        n = counter["n"]
        data = {
            "full_name": f"Test {role} {n}",
            "email": f"{role}{n}@example.com",
            "phone": f"+42070000{n:04d}",
            "password": "Str0ng!Pass-word",
            "role": role,
        }
        data.update(overrides)
        return crud_user.create_user(db, UserCreate(**data))

    return _make


@pytest.fixture
def auth_headers():
    def _headers(user):
        token = create_access_token({"sub": str(user.id), "type": "access"})
        return {"Authorization": f"Bearer {token}"}

    return _headers
