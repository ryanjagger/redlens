"""OpenRouter chat-completions client."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx


class OpenRouterClientError(RuntimeError):
    """Raised when OpenRouter cannot be reached or returns invalid output."""


@dataclass(frozen=True)
class OpenRouterChatResult:
    response_id: str
    model: str
    content: str
    usage: dict[str, Any]
    raw_response: dict[str, Any]

    @property
    def cost_usd(self) -> float | None:
        cost = self.usage.get("cost")
        return cost if isinstance(cost, float | int) else None


class OpenRouterClient:
    """Thin async wrapper around OpenRouter's chat completions endpoint."""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = "https://openrouter.ai/api/v1",
        site_url: str | None = None,
        app_title: str = "RedLens",
        timeout_seconds: float = 30.0,
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._site_url = site_url
        self._app_title = app_title
        self._timeout_seconds = timeout_seconds

    async def chat_completion(
        self,
        *,
        model: str,
        messages: list[dict[str, str]],
        temperature: float,
        max_completion_tokens: int,
        metadata: dict[str, str] | None = None,
        response_format: dict[str, Any] | None = None,
    ) -> OpenRouterChatResult:
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_completion_tokens": max_completion_tokens,
        }
        if metadata:
            payload["metadata"] = metadata
        if response_format:
            payload["response_format"] = response_format

        try:
            async with httpx.AsyncClient(timeout=self._timeout_seconds) as http:
                response = await http.post(
                    f"{self._base_url}/chat/completions",
                    json=payload,
                    headers=self._headers(),
                )
        except httpx.HTTPError as exc:
            raise OpenRouterClientError(f"chat completion request failed: {exc}") from exc

        if response.status_code >= 400:
            raise OpenRouterClientError(
                f"chat completion returned {response.status_code}: {response.text[:500]}",
            )

        try:
            body = response.json()
        except ValueError as exc:
            raise OpenRouterClientError("chat completion returned a non-JSON body") from exc

        return self._parse_chat_result(body)

    def _headers(self) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
            "X-OpenRouter-Experimental-Metadata": "enabled",
        }
        if self._site_url:
            headers["HTTP-Referer"] = self._site_url
        if self._app_title:
            headers["X-OpenRouter-Title"] = self._app_title
        return headers

    @staticmethod
    def _parse_chat_result(body: dict[str, Any]) -> OpenRouterChatResult:
        choices = body.get("choices")
        if not isinstance(choices, list) or not choices:
            raise OpenRouterClientError("chat completion response missing choices")

        first = choices[0]
        if not isinstance(first, dict):
            raise OpenRouterClientError("chat completion choice is invalid")
        message = first.get("message")
        if not isinstance(message, dict):
            raise OpenRouterClientError("chat completion choice missing message")
        content = message.get("content")
        if not isinstance(content, str) or not content:
            raise OpenRouterClientError("chat completion message missing content")

        response_id = body.get("id")
        model = body.get("model")
        usage = body.get("usage", {})
        if not isinstance(response_id, str) or not response_id:
            raise OpenRouterClientError("chat completion response missing id")
        if not isinstance(model, str) or not model:
            raise OpenRouterClientError("chat completion response missing model")
        if not isinstance(usage, dict):
            usage = {}

        return OpenRouterChatResult(
            response_id=response_id,
            model=model,
            content=content,
            usage=usage,
            raw_response=body,
        )
