from sqlalchemy import text
from sqlmodel import Session, SQLModel, create_engine

from app.core.config import get_settings

settings = get_settings()
engine = create_engine(settings.database_url, pool_pre_ping=True)


def init_db() -> None:
    SQLModel.metadata.create_all(engine)
    ensure_database_compatibility()


def ensure_database_compatibility() -> None:
    if not settings.database_url.startswith("postgresql"):
        return
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
        enum_exists = connection.execute(text("select 1 from pg_type where typname = 'evaluationstatus'")).scalar()
        if enum_exists:
            connection.execute(text("ALTER TYPE evaluationstatus ADD VALUE IF NOT EXISTS 'queued'"))


def get_session():
    with Session(engine) as session:
        yield session
