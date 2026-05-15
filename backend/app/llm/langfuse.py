"""Langfuse helpers for RedLens LLM observability."""

from __future__ import annotations

import re
from contextlib import contextmanager
from dataclasses import dataclass
from functools import lru_cache
from typing import TYPE_CHECKING, Any, Iterator

if TYPE_CHECKING:
    from app.config import Settings

_REDACTED = "<redacted>"
_SENSITIVE_KEY_PARTS = (
    "api_key",
    "authorization",
    "bearer",
    "password",
    "patient_uuid",
    "secret",
    "token",
    "user_uuid",
)
_BEARER_PATTERN = re.compile(r"Bearer\s+[A-Za-z0-9._~+/=-]+", re.IGNORECASE)
_UUID_PATTERN = re.compile(
    r"\b[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\b",
    re.IGNORECASE,
)


@dataclass
class LangfuseTraceMetadata:
    trace_id: str
    observation_id: str
    host: str

    def as_dict(self) -> dict[str, str]:
        return {
            "trace_id": self.trace_id,
            "observation_id": self.observation_id,
            "host": self.host,
            "trace_url": f"{self.host}/trace/{self.trace_id}",
        }


@dataclass
class LangfuseCallTrace:
    name: str
    metadata: LangfuseTraceMetadata

    @property
    def trace_id(self) -> str:
        return self.metadata.trace_id

    @property
    def parent_observation_id(self) -> str:
        return self.metadata.observation_id

    def update_output(self, output: Any) -> None:
        """Update the parent RedLens span when supported by the SDK."""

    def update_error(self, error: str, *, output: Any | None = None) -> None:
        """Mark the observation as errored when supported by the SDK."""


@dataclass
class _ActiveLangfuseCallTrace(LangfuseCallTrace):
    observation: Any

    def update_output(self, output: Any) -> None:
        try:
            self.observation.update(output=redlens_langfuse_mask(data=output))
        except Exception:  # noqa: BLE001 - observability must not affect execution
            return

    def update_error(self, error: str, *, output: Any | None = None) -> None:
        try:
            self.observation.update(
                level="ERROR",
                status_message=error,
                output=redlens_langfuse_mask(data=output) if output is not None else None,
            )
        except Exception:  # noqa: BLE001 - observability must not affect execution
            return


def langfuse_is_configured(settings: Settings) -> bool:
    return bool(settings.langfuse_public_key and settings.langfuse_secret_key)


@contextmanager
def redlens_langfuse_observation(
    *,
    settings: Settings,
    name: str,
    metadata: dict[str, str] | None,
    input_payload: Any,
) -> Iterator[LangfuseCallTrace | None]:
    if not langfuse_is_configured(settings):
        yield None
        return

    try:
        from langfuse import propagate_attributes
    except Exception:  # noqa: BLE001 - tracing is optional
        yield None
        return

    normalized_metadata = metadata or {}
    role = normalized_metadata.get("role", "llm")
    prompt_version = normalized_metadata.get("prompt_version")
    tags = ["redlens", role]
    if prompt_version:
        tags.append(prompt_version)

    try:
        langfuse = _get_langfuse_client(
            public_key=settings.langfuse_public_key or "",
            secret_key=settings.langfuse_secret_key or "",
            base_url=settings.langfuse_base_url,
            environment=settings.langfuse_environment,
        )
    except Exception:  # noqa: BLE001 - tracing is optional
        yield None
        return
    set_trace_name = langfuse.get_current_trace_id() is None
    with langfuse.start_as_current_observation(
        as_type="span",
        name=name,
        input=redlens_langfuse_mask(data=input_payload),
        metadata=normalized_metadata,
    ) as observation:
        trace = _ActiveLangfuseCallTrace(
            name=name,
            observation=observation,
            metadata=LangfuseTraceMetadata(
                trace_id=observation.trace_id,
                observation_id=observation.id,
                host=settings.langfuse_base_url.rstrip("/"),
            ),
        )
        with propagate_attributes(
            session_id=_session_id(normalized_metadata),
            metadata=normalized_metadata,
            tags=tags,
            trace_name=name if set_trace_name else None,
        ):
            yield trace


def redlens_langfuse_flush(settings: Settings) -> None:
    if not langfuse_is_configured(settings):
        return
    try:
        langfuse = _get_langfuse_client(
            public_key=settings.langfuse_public_key or "",
            secret_key=settings.langfuse_secret_key or "",
            base_url=settings.langfuse_base_url,
            environment=settings.langfuse_environment,
        )
        langfuse.flush()
    except Exception:  # noqa: BLE001 - tracing is optional
        return


@lru_cache(maxsize=8)
def _get_langfuse_client(*, public_key: str, secret_key: str, base_url: str, environment: str) -> Any:
    from langfuse import Langfuse

    return Langfuse(
        public_key=public_key,
        secret_key=secret_key,
        base_url=base_url,
        environment=environment,
        mask=redlens_langfuse_mask,
    )


def redlens_langfuse_mask(*, data: Any, **_: Any) -> Any:
    if isinstance(data, dict):
        masked: dict[Any, Any] = {}
        for key, value in data.items():
            key_text = str(key).lower()
            if any(part in key_text for part in _SENSITIVE_KEY_PARTS):
                masked[key] = _REDACTED
            else:
                masked[key] = redlens_langfuse_mask(data=value)
        return masked
    if isinstance(data, list):
        return [redlens_langfuse_mask(data=item) for item in data]
    if isinstance(data, tuple):
        return tuple(redlens_langfuse_mask(data=item) for item in data)
    if isinstance(data, str):
        data = _BEARER_PATTERN.sub(f"Bearer {_REDACTED}", data)
        return _UUID_PATTERN.sub(_REDACTED, data)
    return data


def _session_id(metadata: dict[str, str]) -> str | None:
    campaign_id = metadata.get("campaign_id")
    if not campaign_id:
        return None
    return f"campaign-{campaign_id}"
