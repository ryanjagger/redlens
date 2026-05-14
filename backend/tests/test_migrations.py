from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text

from app import models as _models  # noqa: F401
from app.database import Base


BACKEND_ROOT = Path(__file__).resolve().parents[1]


def test_migrations_repair_partially_upgraded_sqlite_schema(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "redlens.db"
    database_url = f"sqlite:///{db_path}"
    monkeypatch.setenv("REDLENS_DATABASE_URL", database_url)

    config = _alembic_config()
    command.upgrade(config, "0001_initial")

    engine = create_engine(database_url, future=True)
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        _add_column_if_missing(conn, "targets", "user_uuid", "VARCHAR(120)")
        _add_column_if_missing(conn, "evaluation_runs", "campaign_id", "INTEGER")
        _add_column_if_missing(conn, "findings", "linked_attempt_id", "INTEGER")
        _add_column_if_missing(conn, "findings", "linked_evaluation_id", "INTEGER")
        _add_column_if_missing(conn, "findings", "report_path", "VARCHAR(500)")
    engine.dispose()

    command.upgrade(config, "head")

    engine = create_engine(database_url, future=True)
    with engine.connect() as conn:
        finding_columns = {
            row[1]: row
            for row in conn.execute(text("PRAGMA table_info(findings)"))
        }
        indexes = {
            row[1]
            for row in conn.execute(text("PRAGMA index_list(findings)"))
        }
        version = conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()

    assert finding_columns["result_id"][3] == 0
    assert "ix_findings_linked_attempt_id" in indexes
    assert "ix_findings_linked_evaluation_id" in indexes
    assert version == "0004_campaign_langfuse"


def _alembic_config() -> Config:
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    config.set_main_option("prepend_sys_path", str(BACKEND_ROOT))
    return config


def _add_column_if_missing(conn, table_name: str, column_name: str, column_type: str) -> None:
    columns = {row[1] for row in conn.execute(text(f"PRAGMA table_info({table_name})"))}
    if column_name not in columns:
        conn.execute(text(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_type}"))
