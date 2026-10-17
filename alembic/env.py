import sys
from pathlib import Path
from logging.config import fileConfig
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sqlalchemy import engine_from_config, pool
from alembic import context
from app.api.core.config import settings
from app.db.base import Base
from app.db import models  # noqa: F401
config = context.config
if config.config_file_name is not None:
    # keep loggers created before migrations run (app modules imported first) alive
    fileConfig(config.config_file_name, disable_existing_loggers=False)
target_metadata = Base.metadata
def run_migrations_offline():
    url = settings.database_url
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()
def run_migrations_online():
    configuration = config.get_section(config.config_ini_section) or {}
    configuration["sqlalchemy.url"] = settings.database_url
    connectable = engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
        future=True,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()
if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
