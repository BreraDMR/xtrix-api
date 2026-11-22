from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.api.core.config import settings
# Neon and other proxies can close idle connections while they remain in our pool.
engine = create_engine(
    settings.database_url,
    future=True,
    pool_pre_ping=True,
    pool_size=settings.db_pool_size,
    max_overflow=settings.db_max_overflow,
    pool_timeout=settings.db_pool_timeout,
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
