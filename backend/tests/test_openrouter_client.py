from __future__ import annotations

import asyncio
from contextlib import contextmanager
from typing import Any

import httpx
import pytest
import respx

from app.llm import openrouter
from app.llm.langfuse import redlens_langfuse_mask
from app.llm.openrouter import OpenRouterClient, OpenRouterClientError

_BASE_URL = "https://openrouter.test/api/v1"
_API_KEY = "or-test-key"


@pytest.fixture(autouse=True)
def _clear_langfuse_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_HOST", raising=False)
    monkeypatch.delenv("LANGFUSE_BASE_URL", raising=False)
    monkeypatch.delenv("LANGFUSE_ENVIRONMENT", raising=False)


def _client() -> OpenRouterClient:
    return OpenRouterClient(
        api_key=_API_KEY,
        base_url=_BASE_URL,
        site_url="http://localhost:5173",
        app_title="RedLens Test",
    )


@respx.mock
def test_chat_completion_sends_headers_and_normalizes_response() -> None:
    route = respx.post(f"{_BASE_URL}/chat/completions").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "gen-123",
                "model": "test/model",
                "choices": [{"message": {"role": "assistant", "content": "{\"goal\":\"probe\"}"}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15, "cost": 0.001},
            },
        )
    )

    result = asyncio.run(
        _client().chat_completion(
            model="test/model",
            messages=[{"role": "user", "content": "plan"}],
            temperature=0.7,
            max_completion_tokens=100,
            metadata={"campaign_id": "1"},
            response_format={"type": "json_object"},
        )
    )

    assert result.response_id == "gen-123"
    assert result.model == "test/model"
    assert result.content == "{\"goal\":\"probe\"}"
    assert result.usage["total_tokens"] == 15
    assert result.cost_usd == 0.001

    request = route.calls.last.request
    assert request.headers["authorization"] == f"Bearer {_API_KEY}"
    assert request.headers["http-referer"] == "http://localhost:5173"
    assert request.headers["x-openrouter-title"] == "RedLens Test"
    body = request.read().decode("utf-8")
    assert '"model":"test/model"' in body
    assert '"response_format":{"type":"json_object"}' in body


def test_chat_completion_passes_langfuse_trace_context(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-test")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-test")
    monkeypatch.setenv("LANGFUSE_BASE_URL", "https://langfuse.test")
    captured: dict[str, Any] = {}

    @contextmanager
    def fake_observation(**kwargs: Any) -> Any:
        captured["observation_kwargs"] = kwargs
        yield _FakeTrace()

    monkeypatch.setattr(openrouter, "redlens_langfuse_observation", fake_observation)
    monkeypatch.setattr(OpenRouterClient, "_openai_client", _fake_openai_client(captured))

    result = asyncio.run(
        _client().chat_completion(
            model="test/model",
            messages=[{"role": "user", "content": "plan"}],
            temperature=0.7,
            max_completion_tokens=100,
            metadata={"campaign_id": "1", "attempt_id": "2", "role": "judge", "prompt_version": "judge_v1"},
            response_format={"type": "json_object"},
        )
    )

    assert captured["tracing_enabled"] is True
    assert captured["observation_kwargs"]["name"] == "redlens.judge"
    assert captured["observation_kwargs"]["metadata"]["attempt_id"] == "2"
    request_kwargs = captured["request_kwargs"]
    assert request_kwargs["name"] == "redlens.judge.generation"
    assert request_kwargs["trace_id"] == "trace-123"
    assert request_kwargs["parent_observation_id"] == "span-456"
    assert request_kwargs["langfuse_public_key"] == "pk-test"
    assert request_kwargs["extra_body"] == {"usage": {"include": True}}
    assert result.langfuse_metadata == {
        "trace_id": "trace-123",
        "observation_id": "span-456",
        "host": "https://langfuse.test",
        "trace_url": "https://langfuse.test/trace/trace-123",
    }


def test_langfuse_mask_redacts_sensitive_values() -> None:
    payload = {
        "Authorization": "Bearer jwt.secret",
        "patient_uuid": "3d5fd468-e0a8-44c8-a8a3-e2b1f9664eb1",
        "nested": {"content": "patient 3d5fd468-e0a8-44c8-a8a3-e2b1f9664eb1"},
    }

    assert redlens_langfuse_mask(data=payload) == {
        "Authorization": "<redacted>",
        "patient_uuid": "<redacted>",
        "nested": {"content": "patient <redacted>"},
    }


@respx.mock
def test_chat_completion_raises_on_upstream_error() -> None:
    respx.post(f"{_BASE_URL}/chat/completions").mock(
        return_value=httpx.Response(401, json={"error": "unauthorized"})
    )

    with pytest.raises(OpenRouterClientError, match="401"):
        asyncio.run(
            _client().chat_completion(
                model="test/model",
                messages=[{"role": "user", "content": "plan"}],
                temperature=0.7,
                max_completion_tokens=100,
            )
        )


@respx.mock
def test_chat_completion_raises_on_missing_content() -> None:
    respx.post(f"{_BASE_URL}/chat/completions").mock(
        return_value=httpx.Response(200, json={"id": "gen-123", "model": "test/model", "choices": []})
    )

    with pytest.raises(OpenRouterClientError, match="missing choices"):
        asyncio.run(
            _client().chat_completion(
                model="test/model",
                messages=[{"role": "user", "content": "plan"}],
                temperature=0.7,
                max_completion_tokens=100,
            )
        )


class _FakeTrace:
    trace_id = "trace-123"
    parent_observation_id = "span-456"

    def update_output(self, output: Any) -> None:
        self.output = output


class _FakeOpenAIModel:
    def __init__(self, **kwargs: Any) -> None:
        self.__dict__.update(kwargs)

    def model_dump(self, *, exclude_none: bool = True) -> dict[str, Any]:
        return {
            key: value
            for key, value in self.__dict__.items()
            if not exclude_none or value is not None
        }


class _FakeCompletions:
    def __init__(self, captured: dict[str, Any]) -> None:
        self._captured = captured

    async def create(self, **kwargs: Any) -> _FakeOpenAIModel:
        self._captured["request_kwargs"] = kwargs
        return _FakeOpenAIModel(
            id="gen-123",
            model="test/model",
            choices=[_FakeOpenAIModel(message=_FakeOpenAIModel(content="{\"goal\":\"probe\"}"))],
            usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15, "cost": 0.001},
        )


class _FakeChat:
    def __init__(self, captured: dict[str, Any]) -> None:
        self.completions = _FakeCompletions(captured)


class _FakeOpenAIClient:
    def __init__(self, captured: dict[str, Any]) -> None:
        self.chat = _FakeChat(captured)


def _fake_openai_client(captured: dict[str, Any]) -> Any:
    def fake_openai_client(self: OpenRouterClient, *, tracing_enabled: bool) -> _FakeOpenAIClient:
        captured["tracing_enabled"] = tracing_enabled
        return _FakeOpenAIClient(captured)

    return fake_openai_client
