"""Runtime configuration for the RedLens backend."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    database_url: str
    cors_origins: tuple[str, ...]
    oe_ai_agent_api_key: str | None
    openrouter_api_key: str | None
    openrouter_base_url: str
    openrouter_site_url: str | None
    openrouter_app_title: str
    red_team_model: str | None
    judge_model: str | None
    documenter_model: str | None
    langfuse_public_key: str | None
    langfuse_secret_key: str | None
    langfuse_base_url: str
    langfuse_environment: str
    findings_dir: Path


def load_settings() -> Settings:
    origins = os.environ.get(
        "REDLENS_CORS_ORIGINS",
        "http://127.0.0.1:5173,http://localhost:5173",
    )
    return Settings(
        database_url=os.environ.get(
            "REDLENS_DATABASE_URL",
            "postgresql+psycopg://redlens:redlens@127.0.0.1:5432/redlens",
        ),
        cors_origins=tuple(origin.strip() for origin in origins.split(",") if origin.strip()),
        oe_ai_agent_api_key=os.environ.get("OE_AI_AGENT_API_KEY"),
        openrouter_api_key=os.environ.get("OPENROUTER_API_KEY"),
        openrouter_base_url=os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"),
        openrouter_site_url=os.environ.get("OPENROUTER_SITE_URL"),
        openrouter_app_title=os.environ.get("OPENROUTER_APP_TITLE", "RedLens"),
        red_team_model=os.environ.get("REDLENS_RED_TEAM_MODEL"),
        judge_model=os.environ.get("REDLENS_JUDGE_MODEL"),
        documenter_model=os.environ.get("REDLENS_DOCUMENTER_MODEL"),
        langfuse_public_key=os.environ.get("LANGFUSE_PUBLIC_KEY"),
        langfuse_secret_key=os.environ.get("LANGFUSE_SECRET_KEY"),
        langfuse_base_url=os.environ.get(
            "LANGFUSE_BASE_URL",
            os.environ.get("LANGFUSE_HOST", "https://cloud.langfuse.com"),
        ),
        langfuse_environment=os.environ.get("LANGFUSE_ENVIRONMENT", "local"),
        findings_dir=Path(
            os.environ.get(
                "REDLENS_FINDINGS_DIR",
                str(Path(__file__).resolve().parents[2] / "docs" / "findings"),
            )
        ),
    )
