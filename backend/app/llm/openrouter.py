"""OpenRouter chat-completions client."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from openai import APIStatusError, AsyncOpenAI, OpenAIError

from app.config import load_settings
from app.llm.langfuse import redlens_langfuse_observation


class OpenRouterClientError(RuntimeError):
    """Raised when OpenRouter cannot be reached or returns invalid output."""


@dataclass(frozen=True)
class OpenRouterChatResult:
    response_id: str
    model: str
    content: str
    usage: dict[str, Any]
    raw_response: dict[str, Any]
    langfuse_trace_id: str | None = None
    langfuse_observation_id: str | None = None
    langfuse_host: str | None = None

    @property
    def cost_usd(self) -> float | None:
        cost = self.usage.get("cost")
        return cost if isinstance(cost, float | int) else None

    @property
    def langfuse_metadata(self) -> dict[str, str] | None:
        if not self.langfuse_trace_id or not self.langfuse_observation_id or not self.langfuse_host:
            return None
        return {
            "trace_id": self.langfuse_trace_id,
            "observation_id": self.langfuse_observation_id,
            "host": self.langfuse_host,
            "trace_url": f"{self.langfuse_host}/trace/{self.langfuse_trace_id}",
        }


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
        settings = load_settings()
        request_kwargs: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_completion_tokens": max_completion_tokens,
            "extra_body": {"usage": {"include": True}},
        }
        if metadata:
            request_kwargs["metadata"] = metadata
        if response_format:
            request_kwargs["response_format"] = response_format

        try:
            trace_name = f"redlens.{(metadata or {}).get('role', 'llm')}"
            with redlens_langfuse_observation(
                settings=settings,
                name=trace_name,
                metadata=metadata,
                input_payload={"model": model, "messages": messages},
            ) as trace:
                client = self._openai_client(tracing_enabled=trace is not None)
                if trace is not None:
                    request_kwargs.update(
                        {
                            "name": f"{trace_name}.generation",
                            "trace_id": trace.trace_id,
                            "parent_observation_id": trace.parent_observation_id,
                            "langfuse_public_key": settings.langfuse_public_key,
                        }
                    )
                response = await client.chat.completions.create(**request_kwargs)
                result = self._parse_chat_result(response)
                if trace is not None:
                    trace.update_output(
                        {
                            "response_id": result.response_id,
                            "model": result.model,
                            "usage": result.usage,
                            "cost_usd": result.cost_usd,
                        }
                    )
                    result = OpenRouterChatResult(
                        response_id=result.response_id,
                        model=result.model,
                        content=result.content,
                        usage=result.usage,
                        raw_response=result.raw_response,
                        langfuse_trace_id=trace.trace_id,
                        langfuse_observation_id=trace.parent_observation_id,
                        langfuse_host=settings.langfuse_base_url.rstrip("/"),
                    )
                return result
        except APIStatusError as exc:
            raise OpenRouterClientError(
                f"chat completion returned {exc.status_code}: {exc.response.text[:500]}",
            ) from exc
        except OpenAIError as exc:
            raise OpenRouterClientError(f"chat completion request failed: {exc}") from exc

    def _openai_client(self, *, tracing_enabled: bool) -> AsyncOpenAI:
        client_cls: type[AsyncOpenAI]
        if tracing_enabled:
            from langfuse.openai import AsyncOpenAI as LangfuseAsyncOpenAI

            client_cls = LangfuseAsyncOpenAI
        else:
            client_cls = AsyncOpenAI

        return client_cls(
            api_key=self._api_key,
            base_url=self._base_url,
            timeout=self._timeout_seconds,
            max_retries=0,
            default_headers=self._headers(),
        )

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
    def _parse_chat_result(body: Any) -> OpenRouterChatResult:
        choices = getattr(body, "choices", None)
        if not isinstance(choices, list) or not choices:
            raise OpenRouterClientError("chat completion response missing choices")

        first = choices[0]
        message = getattr(first, "message", None)
        if message is None:
            raise OpenRouterClientError("chat completion choice missing message")
        content = getattr(message, "content", None)
        if not isinstance(content, str) or not content:
            raise OpenRouterClientError("chat completion message missing content")

        response_id = getattr(body, "id", None)
        model = getattr(body, "model", None)
        usage = _model_to_dict(getattr(body, "usage", None))
        raw_response = _model_to_dict(body)
        if not isinstance(response_id, str) or not response_id:
            raise OpenRouterClientError("chat completion response missing id")
        if not isinstance(model, str) or not model:
            raise OpenRouterClientError("chat completion response missing model")

        return OpenRouterChatResult(
            response_id=response_id,
            model=model,
            content=content,
            usage=usage,
            raw_response=raw_response,
        )


def _model_to_dict(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    if hasattr(value, "model_dump"):
        data = value.model_dump(exclude_none=True)
        extra = getattr(value, "__pydantic_extra__", None)
        if isinstance(extra, dict):
            data.update(extra)
        return data if isinstance(data, dict) else {}
    return {}
