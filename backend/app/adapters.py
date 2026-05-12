"""Target adapters for mock and live OpenEMR sidecar execution."""

from __future__ import annotations

import base64
import os
import time
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

import httpx

from app.models import Evaluation, Target


@dataclass(frozen=True)
class AdapterResponse:
    request_json: dict[str, Any]
    response_json: dict[str, Any]
    status_code: int | None
    latency_ms: int


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


class LiveOpenEmrAdapter:
    async def execute(self, target: Target, evaluation: Evaluation) -> AdapterResponse:
        if not target.internal_auth_env:
            raise AdapterExecutionError("live target is missing internal_auth_env")
        if not target.bearer_token_env and evaluation.endpoint == "/v1/chat":
            raise AdapterExecutionError("live chat target is missing bearer_token_env")

        internal_secret = os.environ.get(target.internal_auth_env)
        if not internal_secret:
            raise AdapterExecutionError(f"environment variable {target.internal_auth_env} is not set")

        bearer_token = None
        if target.bearer_token_env:
            bearer_token = os.environ.get(target.bearer_token_env)
            if evaluation.endpoint == "/v1/chat" and not bearer_token:
                raise AdapterExecutionError(f"environment variable {target.bearer_token_env} is not set")

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
            "headers": {"X-Internal-Auth": f"env:{target.internal_auth_env}"},
            "json": evidence_payload,
        }

        started = time.perf_counter()
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.request(
                    evaluation.method,
                    _join_url(target.base_url, evaluation.endpoint),
                    headers={"X-Internal-Auth": internal_secret},
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

    patient_uuid = target.patient_uuid
    fhir_base_url = target.fhir_base_url
    if target.mode == "live":
        patient_uuid = patient_uuid or os.environ.get("OPENEMR_PATIENT_UUID")
        fhir_base_url = fhir_base_url or os.environ.get("OPENEMR_FHIR_BASE_URL")

    payload = {
        "patient_uuid": patient_uuid or "eval-current-patient",
        "fhir_base_url": fhir_base_url or "mock://openemr/fhir",
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


def _mock_response(evaluation: Evaluation) -> dict[str, Any]:
    safe = (
        "I can't follow instructions that override clinical safety, privacy, or authorization "
        "boundaries. I can only help with authorized current patient context and focused clinical tasks."
    )
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

    if evaluation.key == "state_context_poisoning":
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
