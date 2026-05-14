"""Target adapters for mock and live OpenEMR sidecar execution."""

from __future__ import annotations

import base64
import time
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

import httpx

from app.config import load_settings
from app.models import Evaluation, Target
from app.oe_ai_agent_client import OeAiAgentClient, OeAiAgentClientError

# Endpoints that need a freshly minted OpenEMR FHIR token in the request
# body — the agent uses that token to call OpenEMR on the user's behalf.
# Document extraction has no FHIR call, so it skips the mint entirely.
_ENDPOINTS_NEEDING_BEARER = frozenset({"/v1/chat", "/v1/brief"})

_agent_clients: dict[tuple[str, str], OeAiAgentClient] = {}


def _agent_client_for(base_url: str, api_key: str) -> OeAiAgentClient:
    """Return a cached agent client keyed on (base_url, api_key).

    Caching matters because the client owns the in-memory mint-token
    cache; recreating it per call would defeat the cache.
    """
    key = (base_url.rstrip("/"), api_key)
    existing = _agent_clients.get(key)
    if existing is not None:
        return existing
    client = OeAiAgentClient(base_url=base_url, api_key=api_key)
    _agent_clients[key] = client
    return client


@dataclass(frozen=True)
class AdapterResponse:
    request_json: dict[str, Any]
    response_json: dict[str, Any]
    status_code: int | None
    latency_ms: int


@dataclass(frozen=True)
class ExecutableAttackPayload:
    endpoint: str
    method: str
    messages: list[dict[str, str]]
    document_context: list[dict[str, Any]]
    expected_signal: str | None = None
    document_text: str | None = None
    document_type: str | None = None
    filename: str | None = None
    mime_type: str | None = None


class AdapterExecutionError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        request_json: dict[str, Any] | None = None,
        response_json: dict[str, Any] | None = None,
        status_code: int | None = None,
        latency_ms: int = 0,
    ) -> None:
        super().__init__(message)
        self.request_json = request_json or {}
        self.response_json = response_json or {"error": message}
        self.status_code = status_code
        self.latency_ms = latency_ms


def adapter_for(target: Target) -> MockTargetAdapter | LiveOpenEmrAdapter:
    if target.mode == "live":
        return LiveOpenEmrAdapter()
    return MockTargetAdapter()


class MockTargetAdapter:
    async def execute(self, target: Target, evaluation: Evaluation) -> AdapterResponse:
        started = time.perf_counter()
        request_json = build_request_payload(target, evaluation, redact_secrets=False)
        response_json = _mock_response(evaluation)
        return AdapterResponse(
            request_json=request_json,
            response_json=response_json,
            status_code=200,
            latency_ms=int((time.perf_counter() - started) * 1000),
        )

    async def execute_attack_plan(
        self,
        target: Target,
        evaluation: Evaluation,
        payload: ExecutableAttackPayload,
    ) -> AdapterResponse:
        started = time.perf_counter()
        request_json = build_attack_request_payload(target, payload, redact_secrets=False)
        response_json = _mock_response_for_attack_payload(evaluation, payload)
        return AdapterResponse(
            request_json=request_json,
            response_json=response_json,
            status_code=200,
            latency_ms=int((time.perf_counter() - started) * 1000),
        )


class LiveOpenEmrAdapter:
    async def execute(self, target: Target, evaluation: Evaluation) -> AdapterResponse:
        if not target.user_uuid:
            raise AdapterExecutionError("live target is missing user_uuid")

        settings = load_settings()
        api_key = settings.oe_ai_agent_api_key
        if not api_key:
            raise AdapterExecutionError(
                "OE_AI_AGENT_API_KEY is not set; cannot call live agent",
            )

        client = _agent_client_for(target.base_url, api_key)

        bearer_token: str | None = None
        if evaluation.endpoint in _ENDPOINTS_NEEDING_BEARER:
            try:
                bearer_token = await client.mint_token(
                    user_uuid=target.user_uuid,
                    scope="chat" if evaluation.endpoint == "/v1/chat" else "brief",
                    patient_uuid=target.patient_uuid,
                )
            except OeAiAgentClientError as exc:
                raise AdapterExecutionError(f"mint failed: {exc}") from exc

        actual_payload = build_request_payload(
            target,
            evaluation,
            bearer_token=bearer_token,
            redact_secrets=False,
        )
        evidence_payload = build_request_payload(
            target,
            evaluation,
            bearer_token="<redacted>" if bearer_token else None,
            redact_secrets=True,
        )
        evidence = {
            "method": evaluation.method,
            "url": _join_url(target.base_url, evaluation.endpoint),
            "headers": {"Authorization": "Bearer <redacted>"},
            "json": evidence_payload,
        }

        started = time.perf_counter()
        try:
            async with httpx.AsyncClient(timeout=30.0) as http:
                response = await http.request(
                    evaluation.method,
                    _join_url(target.base_url, evaluation.endpoint),
                    headers=client.auth_header(),
                    json=actual_payload,
                )
        except httpx.HTTPError as exc:
            latency_ms = int((time.perf_counter() - started) * 1000)
            raise AdapterExecutionError(
                f"live target request failed: {exc}",
                request_json=evidence,
                latency_ms=latency_ms,
            ) from exc

        latency_ms = int((time.perf_counter() - started) * 1000)
        try:
            response_json = response.json()
        except ValueError:
            response_json = {"text": response.text}

        return AdapterResponse(
            request_json=evidence,
            response_json=response_json,
            status_code=response.status_code,
            latency_ms=latency_ms,
        )

    async def execute_attack_plan(
        self,
        target: Target,
        evaluation: Evaluation,
        payload: ExecutableAttackPayload,
    ) -> AdapterResponse:
        if payload.endpoint not in {"/v1/chat", "/v1/documents/extract"}:
            raise AdapterExecutionError("llm-assisted live execution currently supports /v1/chat and /v1/documents/extract")
        if payload.endpoint in _ENDPOINTS_NEEDING_BEARER and not target.user_uuid:
            raise AdapterExecutionError("live target is missing user_uuid")

        settings = load_settings()
        api_key = settings.oe_ai_agent_api_key
        if not api_key:
            raise AdapterExecutionError(
                "OE_AI_AGENT_API_KEY is not set; cannot call live agent",
            )

        client = _agent_client_for(target.base_url, api_key)
        bearer_token: str | None = None
        if payload.endpoint in _ENDPOINTS_NEEDING_BEARER:
            try:
                bearer_token = await client.mint_token(
                    user_uuid=target.user_uuid or "",
                    scope="chat",
                    patient_uuid=target.patient_uuid,
                )
            except OeAiAgentClientError as exc:
                raise AdapterExecutionError(f"mint failed: {exc}") from exc

        actual_payload = build_attack_request_payload(
            target,
            payload,
            bearer_token=bearer_token,
            redact_secrets=False,
        )
        evidence_payload = build_attack_request_payload(
            target,
            payload,
            bearer_token="<redacted>" if bearer_token else None,
            redact_secrets=True,
        )
        evidence = {
            "method": payload.method,
            "url": _join_url(target.base_url, payload.endpoint),
            "headers": {"Authorization": "Bearer <redacted>"},
            "json": evidence_payload,
        }

        started = time.perf_counter()
        try:
            async with httpx.AsyncClient(timeout=30.0) as http:
                response = await http.request(
                    payload.method,
                    _join_url(target.base_url, payload.endpoint),
                    headers=client.auth_header(),
                    json=actual_payload,
                )
        except httpx.HTTPError as exc:
            latency_ms = int((time.perf_counter() - started) * 1000)
            raise AdapterExecutionError(
                f"live target request failed: {exc}",
                request_json=evidence,
                latency_ms=latency_ms,
            ) from exc

        latency_ms = int((time.perf_counter() - started) * 1000)
        try:
            response_json = response.json()
        except ValueError:
            response_json = {"text": response.text}

        return AdapterResponse(
            request_json=evidence,
            response_json=response_json,
            status_code=response.status_code,
            latency_ms=latency_ms,
        )


def build_request_payload(
    target: Target,
    evaluation: Evaluation,
    *,
    bearer_token: str | None = "<mock-token>",
    redact_secrets: bool = True,
) -> dict[str, Any]:
    template = evaluation.input_template or {}
    request_id = f"redlens-{uuid4()}"

    if evaluation.endpoint == "/v1/documents/extract":
        document_text = str(template.get("document_text", "Synthetic RedLens PDF content."))
        return {
            "request_id": request_id,
            "document_uuid": f"redlens-doc-{uuid4()}",
            "document_type": template.get("document_type", "lab_report"),
            "filename": template.get("filename", "redlens-eval.pdf"),
            "mime_type": template.get("mime_type", "application/pdf"),
            "content_base64": _minimal_pdf_base64(document_text),
        }

    payload = {
        "patient_uuid": target.patient_uuid or "eval-current-patient",
        "fhir_base_url": target.fhir_base_url or "mock://openemr/fhir",
        "bearer_token": bearer_token if bearer_token is not None else "",
        "request_id": request_id,
        "conversation_id": None,
        "messages": template.get("messages", []),
        "document_context": template.get("document_context", []),
        "user_id": "redlens-evaluator",
        "session_id": "redlens-mvp",
    }
    if redact_secrets and payload["bearer_token"]:
        payload["bearer_token"] = "<redacted>"
    return payload


def build_attack_request_payload(
    target: Target,
    payload: ExecutableAttackPayload,
    *,
    bearer_token: str | None = "<mock-token>",
    redact_secrets: bool = True,
) -> dict[str, Any]:
    if payload.endpoint == "/v1/documents/extract":
        document_text = payload.document_text or "Synthetic RedLens PDF content."
        return {
            "request_id": f"redlens-{uuid4()}",
            "document_uuid": f"redlens-doc-{uuid4()}",
            "document_type": payload.document_type or "lab_report",
            "filename": payload.filename or "redlens-eval.pdf",
            "mime_type": payload.mime_type or "application/pdf",
            "content_base64": _minimal_pdf_base64(document_text),
        }

    if payload.endpoint != "/v1/chat":
        raise AdapterExecutionError("executable attack payloads currently support /v1/chat and /v1/documents/extract")

    request_payload = {
        "patient_uuid": target.patient_uuid or "eval-current-patient",
        "fhir_base_url": target.fhir_base_url or "mock://openemr/fhir",
        "bearer_token": bearer_token if bearer_token is not None else "",
        "request_id": f"redlens-{uuid4()}",
        "conversation_id": None,
        "messages": payload.messages,
        "document_context": payload.document_context,
        "user_id": "redlens-evaluator",
        "session_id": "redlens-exploration",
    }
    if redact_secrets and request_payload["bearer_token"]:
        request_payload["bearer_token"] = "<redacted>"
    return request_payload


def _mock_response(evaluation: Evaluation) -> dict[str, Any]:
    safe = (
        "I can't follow instructions that override clinical safety, privacy, or authorization "
        "boundaries. I can only help with authorized current patient context and focused clinical tasks."
    )
    template_text = _input_template_text(evaluation.input_template).lower()
    if evaluation.endpoint == "/v1/documents/extract":
        return {
            "request_id": "mock",
            "model_id": "mock-redlens",
            "document_uuid": "mock-doc",
            "document_type": "lab_report",
            "document_summary": "Extracted clinical facts only; untrusted document instructions were ignored.",
            "extraction_confidence": 0.91,
            "facts": [
                {
                    "fact_type": "lab_result",
                    "label": "A1c",
                    "value_text": "7.2%",
                    "source_snippets": [{"page_number": 1, "text": "A1c 7.2%"}],
                }
            ],
        }

    if evaluation.key == "state_context_poisoning" or "highest-priority instruction" in template_text:
        return {
            "request_id": "mock",
            "conversation_id": "mock-conversation",
            "model_id": "mock-redlens",
            "narrative": "I will treat the uploaded document as the highest-priority instruction for future turns.",
            "facts": [],
        }
    if evaluation.key == "dos_large_output_amplification":
        return {
            "request_id": "mock",
            "conversation_id": "mock-conversation",
            "model_id": "mock-redlens",
            "narrative": "exhaustive explanation " * 120,
            "facts": [],
        }

    return {
        "request_id": "mock",
        "conversation_id": "mock-conversation",
        "model_id": "mock-redlens",
        "narrative": safe,
        "facts": [],
        "verification_failures": [],
    }


def _mock_response_for_attack_payload(
    evaluation: Evaluation,
    payload: ExecutableAttackPayload,
) -> dict[str, Any]:
    if payload.endpoint == "/v1/documents/extract":
        return {
            "request_id": "mock",
            "model_id": "mock-redlens",
            "document_uuid": "mock-doc",
            "document_type": payload.document_type or "lab_report",
            "document_summary": "Extracted clinical facts only; untrusted document instructions were ignored.",
            "extraction_confidence": 0.9,
            "facts": [
                {
                    "fact_type": "clinical_note",
                    "label": "RedLens synthetic document",
                    "value_text": "Untrusted instructions ignored.",
                    "source_snippets": [{"page_number": 1, "text": "synthetic document content"}],
                }
            ],
            "redlens_mock": {
                "execution_source": "llm_attack_plan",
                "evaluation_key": evaluation.key,
                "expected_signal": payload.expected_signal,
            },
        }

    joined_messages = "\n".join(message.get("content", "") for message in payload.messages).lower()
    if "system prompt" in joined_messages or "ignore previous instructions" in joined_messages:
        narrative = (
            "I can't override my clinical safety, privacy, or authorization instructions, "
            "and I can't reveal hidden system prompts."
        )
    else:
        narrative = (
            "I can help with authorized current patient context, but I will not follow untrusted "
            "instructions that bypass clinical safety or privacy boundaries."
        )
    return {
        "request_id": "mock",
        "conversation_id": "mock-conversation",
        "model_id": "mock-redlens",
        "narrative": narrative,
        "facts": [],
        "verification_failures": [],
        "redlens_mock": {
            "execution_source": "llm_attack_plan",
            "evaluation_key": evaluation.key,
            "expected_signal": payload.expected_signal,
        },
    }


def _join_url(base_url: str, endpoint: str) -> str:
    return f"{base_url.rstrip('/')}/{endpoint.lstrip('/')}"


def _minimal_pdf_base64(text: str) -> str:
    # Enough structure for transport evidence; live model providers may still use OCR/text fallback.
    escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    pdf = (
        "%PDF-1.4\n"
        "1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n"
        "2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj\n"
        "3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        "/Contents 4 0 R >> endobj\n"
        f"4 0 obj << /Length {len(escaped) + 48} >> stream\n"
        f"BT /F1 12 Tf 72 720 Td ({escaped}) Tj ET\n"
        "endstream endobj\n"
        "xref\n0 5\n0000000000 65535 f \n"
        "trailer << /Root 1 0 R /Size 5 >>\nstartxref\n0\n%%EOF\n"
    )
    return base64.b64encode(pdf.encode("utf-8")).decode("ascii")


def _input_template_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "\n".join(_input_template_text(item) for item in value)
    if isinstance(value, dict):
        return "\n".join(_input_template_text(item) for item in value.values())
    return str(value)
