"""Client for the oe-ai-agent sidecar.

RedLens authenticates to oe-ai-agent with a long-lived API key
(``OE_AI_AGENT_API_KEY``). The agent gates its existing endpoints
(``/v1/chat``, ``/v1/documents/extract``) and a new mint endpoint
(``/v1/openemr/mint-token``) on that key. We use the mint endpoint to
obtain a fresh short-lived OpenEMR FHIR token per ``user_uuid``,
caching for slightly less than the token's 5-minute TTL so a typical
multi-evaluation run only mints once per user.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

import httpx


class OeAiAgentClientError(RuntimeError):
    """Raised when the agent cannot be reached or returns an error."""


@dataclass(frozen=True)
class MintedToken:
    access_token: str
    expires_at: float  # monotonic seconds


_TOKEN_REFRESH_BUFFER_SECONDS = 60  # refresh ~1 min before expiry


class OeAiAgentClient:
    """Async client that mints + caches OpenEMR FHIR tokens via the agent."""

    def __init__(self, *, base_url: str, api_key: str, timeout_seconds: float = 10.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._timeout_seconds = timeout_seconds
        self._cache: dict[tuple[str, str], MintedToken] = {}
        self._locks: dict[tuple[str, str], asyncio.Lock] = {}

    async def mint_token(
        self,
        *,
        user_uuid: str,
        scope: str = "chat",
        patient_uuid: str | None = None,
    ) -> str:
        """Return a fresh access token, minting via the agent if needed."""
        cache_key = (user_uuid, scope)
        now = time.monotonic()
        cached = self._cache.get(cache_key)
        if cached is not None and cached.expires_at > now:
            return cached.access_token

        lock = self._locks.setdefault(cache_key, asyncio.Lock())
        async with lock:
            cached = self._cache.get(cache_key)
            if cached is not None and cached.expires_at > now:
                return cached.access_token

            payload: dict[str, str] = {"user_uuid": user_uuid, "scope": scope}
            if patient_uuid is not None:
                payload["patient_uuid"] = patient_uuid

            url = f"{self._base_url}/v1/openemr/mint-token"
            try:
                async with httpx.AsyncClient(timeout=self._timeout_seconds) as http:
                    response = await http.post(
                        url,
                        json=payload,
                        headers={"Authorization": f"Bearer {self._api_key}"},
                    )
            except httpx.HTTPError as exc:
                raise OeAiAgentClientError(f"mint request failed: {exc}") from exc

            if response.status_code >= 400:
                raise OeAiAgentClientError(
                    f"mint returned {response.status_code}: {response.text[:300]}",
                )

            try:
                body = response.json()
            except ValueError as exc:
                raise OeAiAgentClientError("mint returned a non-JSON body") from exc

            access_token = body.get("access_token")
            expires_in = body.get("expires_in_seconds", 300)
            if not isinstance(access_token, str) or not access_token:
                raise OeAiAgentClientError("mint response missing access_token")
            if not isinstance(expires_in, int) or expires_in <= 0:
                raise OeAiAgentClientError("mint response missing expires_in_seconds")

            expires_at = time.monotonic() + max(expires_in - _TOKEN_REFRESH_BUFFER_SECONDS, 30)
            minted = MintedToken(access_token=access_token, expires_at=expires_at)
            self._cache[cache_key] = minted
            return minted.access_token

    def auth_header(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._api_key}"}

    @property
    def base_url(self) -> str:
        return self._base_url
