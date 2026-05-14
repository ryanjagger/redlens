"""Runtime configuration for the RedLens backend."""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    database_url: str
    cors_origins: tuple[str, ...]
    oe_ai_agent_api_key: str | None


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
    )
