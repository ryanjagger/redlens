from __future__ import annotations

import asyncio
import os
from typing import Any

os.environ.setdefault("REDLENS_DATABASE_URL", "sqlite:///:memory:")

import httpx  # noqa: E402
import pytest  # noqa: E402
import respx  # noqa: E402

from app import adapters  # noqa: E402
from app.adapters import (  # noqa: E402
    AdapterExecutionError,
    LiveOpenEmrAdapter,
    MockTargetAdapter,
)
from app.models import Evaluation, Target  # noqa: E402

_AGENT_BASE = "http://agent.test"


def _make_target(**overrides: Any) -> Target:
    base = {
        "id": 1,
        "name": "live",
        "mode": "live",
        "base_url": _AGENT_BASE,
        "user_uuid": "u-1",
        "patient_uuid": "p-1",
        "fhir_base_url": "http://openemr.test/apis/default/fhir",
    }
    base.update(overrides)
    return Target(**base)


def _make_evaluation(*, endpoint: str = "/v1/chat", key: str = "test-eval") -> Evaluation:
    return Evaluation(
        id=1,
        key=key,
        name="test",
        category_id=1,
        endpoint=endpoint,
        method="POST",
        severity="medium",
        input_template={"messages": [{"role": "user", "content": "hi"}]},
        expected_behavior="",
        success_condition="",
        judge={},
        enabled=True,
    )


@pytest.fixture(autouse=True)
def _reset_client_cache() -> None:
    adapters._agent_clients.clear()


def test_mock_adapter_runs_without_user_uuid() -> None:
    target = Target(id=1, name="m", mode="mock", base_url="mock://openemr")
    result = asyncio.run(MockTargetAdapter().execute(target, _make_evaluation()))
    assert result.status_code == 200


def test_live_without_user_uuid_errors() -> None:
    target = _make_target(user_uuid=None)
    with pytest.raises(AdapterExecutionError, match="user_uuid"):
        asyncio.run(LiveOpenEmrAdapter().execute(target, _make_evaluation()))


def test_live_without_api_key_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OE_AI_AGENT_API_KEY", raising=False)
    target = _make_target()
    with pytest.raises(AdapterExecutionError, match="OE_AI_AGENT_API_KEY"):
        asyncio.run(LiveOpenEmrAdapter().execute(target, _make_evaluation()))


@respx.mock
def test_live_chat_mints_token_and_forwards_to_agent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OE_AI_AGENT_API_KEY", "rl-test-key")

    mint_route = respx.post(f"{_AGENT_BASE}/v1/openemr/mint-token").mock(
        return_value=httpx.Response(
            200,
            json={
                "access_token": "jwt.minted",
                "token_type": "Bearer",
                "expires_in_seconds": 300,
                "scope_profile": "chat",
                "user_uuid": "u-1",
                "patient_uuid": "p-1",
            },
        ),
    )
    chat_route = respx.post(f"{_AGENT_BASE}/v1/chat").mock(
        return_value=httpx.Response(
            200,
            json={"narrative": "ok", "facts": []},
        ),
    )

    target = _make_target()
    result = asyncio.run(LiveOpenEmrAdapter().execute(target, _make_evaluation()))

    assert result.status_code == 200
    assert mint_route.called
    assert chat_route.called

    # Token from mint flows into the chat body.
    chat_body = chat_route.calls.last.request.read().decode("utf-8")
    assert '"bearer_token":"jwt.minted"' in chat_body
    # Auth on chat is the API key, not the minted token.
    assert chat_route.calls.last.request.headers["authorization"] == "Bearer rl-test-key"
    # Evidence redacts the bearer token but keeps it as a placeholder.
    assert result.request_json["json"]["bearer_token"] == "<redacted>"
    assert result.request_json["headers"]["Authorization"] == "Bearer <redacted>"


@respx.mock
def test_live_document_extract_skips_mint(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OE_AI_AGENT_API_KEY", "rl-test-key")
    mint_route = respx.post(f"{_AGENT_BASE}/v1/openemr/mint-token")
    extract_route = respx.post(f"{_AGENT_BASE}/v1/documents/extract").mock(
        return_value=httpx.Response(200, json={"facts": []}),
    )

    target = _make_target()
    evaluation = _make_evaluation(endpoint="/v1/documents/extract", key="doc-eval")
    result = asyncio.run(LiveOpenEmrAdapter().execute(target, evaluation))

    assert result.status_code == 200
    assert not mint_route.called
    assert extract_route.called
    # No bearer_token field for document extraction payloads.
    body = extract_route.calls.last.request.read().decode("utf-8")
    assert "bearer_token" not in body


@respx.mock
def test_live_chat_mint_failure_bubbles_up(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OE_AI_AGENT_API_KEY", "rl-test-key")
    respx.post(f"{_AGENT_BASE}/v1/openemr/mint-token").mock(
        return_value=httpx.Response(401, json={"error": "unauthorized"}),
    )

    target = _make_target()
    with pytest.raises(AdapterExecutionError, match="mint failed"):
        asyncio.run(LiveOpenEmrAdapter().execute(target, _make_evaluation()))
