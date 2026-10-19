from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.api.core.config import settings
# Neon and other proxies can close idle connections while they remain in our pool.
engine = create_engine(settings.database_url, future=True, pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
