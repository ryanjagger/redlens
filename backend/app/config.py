"""Runtime configuration for the RedLens backend."""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    database_url: str
    cors_origins: tuple[str, ...]


def load_settings() -> Settings:
    origins = os.environ.get(
        "REDLENS_CORS_ORIGINS",
        "http://127.0.0.1:5173,http://localhost:5173",
    )
    return Settings(
        database_url=os.environ.get("REDLENS_DATABASE_URL", "sqlite:///./redlens.db"),
        cors_origins=tuple(origin.strip() for origin in origins.split(",") if origin.strip()),
    )

