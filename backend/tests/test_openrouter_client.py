from __future__ import annotations

import asyncio

import httpx
import pytest
import respx

from app.llm.openrouter import OpenRouterClient, OpenRouterClientError

_BASE_URL = "https://openrouter.test/api/v1"
_API_KEY = "or-test-key"


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
