from __future__ import annotations

import asyncio
import time

import httpx
import pytest
import respx

from app.oe_ai_agent_client import OeAiAgentClient, OeAiAgentClientError

_BASE_URL = "http://agent.test"
_API_KEY = "rl-test-key"


def _client() -> OeAiAgentClient:
    return OeAiAgentClient(base_url=_BASE_URL, api_key=_API_KEY)


def _mint_response(token: str = "jwt.abc", expires: int = 300) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "access_token": token,
            "token_type": "Bearer",
            "expires_in_seconds": expires,
            "scope_profile": "chat",
            "user_uuid": "u-1",
            "patient_uuid": None,
        },
    )


@respx.mock
def test_mint_returns_token_and_sends_authorization_header() -> None:
    route = respx.post(f"{_BASE_URL}/v1/openemr/mint-token").mock(
        return_value=_mint_response(),
    )

    token = asyncio.run(_client().mint_token(user_uuid="u-1"))

    assert token == "jwt.abc"
    assert route.called
    sent = route.calls.last.request
    assert sent.headers["authorization"] == f"Bearer {_API_KEY}"
    body = sent.read().decode("utf-8")
    assert '"user_uuid":"u-1"' in body
    assert '"scope":"chat"' in body
    assert "patient_uuid" not in body


@respx.mock
def test_mint_passes_patient_uuid_when_provided() -> None:
    route = respx.post(f"{_BASE_URL}/v1/openemr/mint-token").mock(
        return_value=_mint_response(),
    )

    asyncio.run(_client().mint_token(user_uuid="u-1", patient_uuid="p-1"))

    body = route.calls.last.request.read().decode("utf-8")
    assert '"patient_uuid":"p-1"' in body


@respx.mock
def test_mint_caches_token_within_ttl() -> None:
    route = respx.post(f"{_BASE_URL}/v1/openemr/mint-token").mock(
        return_value=_mint_response(),
    )
    client = _client()

    first = asyncio.run(client.mint_token(user_uuid="u-1"))
    second = asyncio.run(client.mint_token(user_uuid="u-1"))

    assert first == second
    assert route.call_count == 1


@respx.mock
def test_mint_separate_cache_per_scope() -> None:
    route = respx.post(f"{_BASE_URL}/v1/openemr/mint-token").mock(
        return_value=_mint_response(),
    )
    client = _client()

    asyncio.run(client.mint_token(user_uuid="u-1", scope="chat"))
    asyncio.run(client.mint_token(user_uuid="u-1", scope="brief"))

    assert route.call_count == 2


@respx.mock
def test_mint_refreshes_after_expiry(monkeypatch: pytest.MonkeyPatch) -> None:
    respx.post(f"{_BASE_URL}/v1/openemr/mint-token").mock(
        side_effect=[_mint_response("jwt.first"), _mint_response("jwt.second")],
    )
    client = _client()

    fake_now = [time.monotonic()]

    def fake_monotonic() -> float:
        return fake_now[0]

    monkeypatch.setattr("app.oe_ai_agent_client.time.monotonic", fake_monotonic)

    first = asyncio.run(client.mint_token(user_uuid="u-1"))
    fake_now[0] += 10_000  # jump past TTL
    second = asyncio.run(client.mint_token(user_uuid="u-1"))

    assert first == "jwt.first"
    assert second == "jwt.second"


@respx.mock
def test_mint_raises_on_upstream_4xx() -> None:
    respx.post(f"{_BASE_URL}/v1/openemr/mint-token").mock(
        return_value=httpx.Response(401, json={"error": "unauthorized"}),
    )

    with pytest.raises(OeAiAgentClientError, match="401"):
        asyncio.run(_client().mint_token(user_uuid="u-1"))


@respx.mock
def test_mint_raises_on_transport_error() -> None:
    respx.post(f"{_BASE_URL}/v1/openemr/mint-token").mock(
        side_effect=httpx.ConnectError("refused"),
    )

    with pytest.raises(OeAiAgentClientError, match="mint request failed"):
        asyncio.run(_client().mint_token(user_uuid="u-1"))


@respx.mock
def test_mint_raises_on_missing_access_token() -> None:
    respx.post(f"{_BASE_URL}/v1/openemr/mint-token").mock(
        return_value=httpx.Response(
            200,
            json={"token_type": "Bearer", "expires_in_seconds": 300},
        ),
    )

    with pytest.raises(OeAiAgentClientError, match="missing access_token"):
        asyncio.run(_client().mint_token(user_uuid="u-1"))
