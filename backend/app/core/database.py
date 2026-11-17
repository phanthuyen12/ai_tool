from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import get_settings

settings = get_settings()

engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,
    pool_size=5,
    max_overflow=5,
    future=True,
)

SessionLocal = sessionmaker(
    bind=engine,
    autocommit=False,
    autoflush=False,
    expire_on_commit=False,
    class_=Session,
)


class Base(DeclarativeBase):
    """Base declarative class for all SQLAlchemy ORM models."""


def get_db() -> Generator[Session, None, None]:
    """Yield a synchronous database session for FastAPI requests."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

