"""Database setup."""

from __future__ import annotations

from collections.abc import Generator

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import load_settings


class Base(DeclarativeBase):
    pass


settings = load_settings()

if settings.database_url == "sqlite:///:memory:":
    engine = create_engine(
        settings.database_url,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
elif settings.database_url.startswith("sqlite"):
    engine = create_engine(
        settings.database_url,
        connect_args={"check_same_thread": False},
        future=True,
    )
else:
    engine = create_engine(settings.database_url, pool_pre_ping=True, future=True)

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


def init_db() -> None:
    from app import models  # noqa: F401

    Base.metadata.create_all(bind=engine)
    _apply_lightweight_migrations()


def _apply_lightweight_migrations() -> None:
    """Idempotent column additions for SQLite.

    ``metadata.create_all`` does not add columns to existing tables.
    We're SQLite-only in deployed environments (per-env volume), so a
    targeted PRAGMA-checked ALTER TABLE keeps the live DB in sync
    without pulling in a full Alembic-on-startup story.
    """
    if not engine.dialect.name.startswith("sqlite"):
        return
    inspector = inspect(engine)
    if "targets" not in inspector.get_table_names():
        return
    existing = {col["name"] for col in inspector.get_columns("targets")}
    if "user_uuid" in existing:
        return
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE targets ADD COLUMN user_uuid VARCHAR(120)"))


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

