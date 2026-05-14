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
    table_names = set(inspector.get_table_names())
    if "targets" not in table_names:
        return
    with engine.begin() as conn:
        _add_column_if_missing(conn, inspector, "targets", "user_uuid", "VARCHAR(120)")
        if "evaluation_runs" in table_names:
            _add_column_if_missing(conn, inspector, "evaluation_runs", "campaign_id", "INTEGER")
        if "findings" in table_names:
            _add_column_if_missing(conn, inspector, "findings", "linked_attempt_id", "INTEGER")
            _add_column_if_missing(conn, inspector, "findings", "linked_evaluation_id", "INTEGER")
            _add_column_if_missing(conn, inspector, "findings", "report_path", "VARCHAR(500)")


def _add_column_if_missing(conn, inspector, table_name: str, column_name: str, column_type: str) -> None:
    existing = {col["name"] for col in inspector.get_columns(table_name)}
    if column_name not in existing:
        conn.execute(text(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_type}"))


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
