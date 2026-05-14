"""Finding report and promotion-draft generation for exploration exploits."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.agents.prompts import DOCUMENTER_REPORT_PROMPT_VERSION, documenter_report_messages
from app.config import load_settings
from app.llm.openrouter import OpenRouterClient
from app.models import Attempt, Campaign, Evaluation, Finding, Verdict

REPORT_PATH_PREFIX = "docs/findings"


@dataclass(frozen=True)
class WrittenReport:
    report_path: str
    file_path: Path
    sha256: str
    size_bytes: int
    generation_metadata: dict[str, Any]


def build_reproduction(
    *,
    campaign: Campaign,
    attempt: Attempt,
    verdict: Verdict,
    evaluation: Evaluation | None,
) -> dict[str, Any]:
    return {
        "campaign_id": campaign.id,
        "attempt_id": attempt.id,
        "target": {
            "id": campaign.target_id,
            "name": campaign.target_name_snapshot,
            "mode": campaign.target_mode_snapshot,
            "base_url": campaign.target_base_url_snapshot,
            "user_uuid": campaign.target_user_uuid_snapshot,
            "patient_uuid": campaign.target_patient_uuid_snapshot,
        },
        "evaluation": _evaluation_summary(evaluation),
        "focus_area": attempt.focus_area,
        "vector_key": attempt.vector_key,
        "request": attempt.request_json,
        "response": attempt.response_json,
        "attack_plan": attempt.attack_plan,
        "execution_metadata": attempt.execution_metadata,
        "verdict": {
            "id": verdict.id,
            "tier": verdict.tier,
            "verdict": verdict.verdict,
            "severity": verdict.severity,
            "confidence": verdict.confidence,
            "rationale": verdict.rationale,
            "judge_model": verdict.judge_model,
            "judge_prompt_version": verdict.judge_prompt_version,
            "judge_temperature": verdict.judge_temperature,
            "raw_output": verdict.raw_output,
        },
    }


def relative_report_path(finding_id: int) -> str:
    return f"{REPORT_PATH_PREFIX}/F-{finding_id:03d}.md"


def draft_evaluation_json(
    *,
    evaluation: Evaluation | None,
    finding: Finding,
    attempt: Attempt,
    verdict: Verdict,
) -> dict[str, Any]:
    endpoint = _endpoint_for_draft(evaluation, attempt)
    method = _method_for_draft(evaluation, attempt)
    input_template = _input_template_for_draft(evaluation, attempt)
    source_key = evaluation.key if evaluation is not None else attempt.vector_key or f"attempt_{attempt.id}"
    source_name = evaluation.name if evaluation is not None else finding.title
    judge = dict(evaluation.judge if evaluation is not None else {})
    judge["redlens_promotion"] = {
        "finding_id": finding.id,
        "attempt_id": attempt.id,
        "verdict_id": verdict.id,
        "verdict_tier": verdict.tier,
        "judge_prompt_version": verdict.judge_prompt_version,
        "rationale": verdict.rationale,
    }

    data: dict[str, Any] = {
        "key": _safe_key(f"promoted_{source_key}_f{finding.id}"),
        "name": f"{source_name} (F-{finding.id:03d})",
        "category_key": finding.category_key,
        "endpoint": endpoint,
        "method": method,
        "severity": finding.severity,
        "input_template": input_template,
        "expected_behavior": (
            evaluation.expected_behavior
            if evaluation is not None
            else "Target should preserve clinical, privacy, and authorization boundaries."
        ),
        "success_condition": (
            evaluation.success_condition
            if evaluation is not None
            else "Target reproduces the exploit behavior documented by the linked finding."
        ),
        "judge": judge,
        "enabled": True,
        "linked_finding_id": finding.id,
        "source_attempt_id": attempt.id,
        "source_verdict_id": verdict.id,
    }
    if evaluation is not None:
        data["category_id"] = evaluation.category_id
    return data


async def render_report_markdown(
    *,
    campaign: Campaign,
    finding: Finding,
    attempt: Attempt,
    verdict: Verdict,
    evaluation: Evaluation | None,
    reproduction: dict[str, Any],
    draft_evaluation: dict[str, Any],
) -> tuple[str, dict[str, Any], float]:
    baseline = _template_report(
        campaign=campaign,
        finding=finding,
        attempt=attempt,
        verdict=verdict,
        evaluation=evaluation,
        reproduction=reproduction,
        draft_evaluation=draft_evaluation,
    )
    settings = load_settings()
    if not settings.openrouter_api_key or not settings.documenter_model:
        return baseline, {"source": "template_documenter"}, 0.0

    requested_temperature = 0.2
    client = OpenRouterClient(
        api_key=settings.openrouter_api_key,
        base_url=settings.openrouter_base_url,
        site_url=settings.openrouter_site_url,
        app_title=settings.openrouter_app_title,
    )
    evidence = {
        "finding": {
            "id": finding.id,
            "title": finding.title,
            "severity": finding.severity,
            "category_key": finding.category_key,
            "endpoint": finding.endpoint,
            "status": finding.status,
        },
        "campaign": {
            "id": campaign.id,
            "target_mode": campaign.target_mode_snapshot,
            "target_name": campaign.target_name_snapshot,
        },
        "attempt": {
            "id": attempt.id,
            "status": attempt.status,
            "focus_area": attempt.focus_area,
            "vector_key": attempt.vector_key,
        },
        "verdict": reproduction["verdict"],
        "reproduction": reproduction,
        "draft_evaluation": draft_evaluation,
    }
    try:
        result = await client.chat_completion(
            model=settings.documenter_model,
            messages=documenter_report_messages(
                finding_id=finding.id,
                deterministic_report=baseline,
                evidence=evidence,
            ),
            temperature=requested_temperature,
            max_completion_tokens=1800,
            metadata={
                "campaign_id": str(campaign.id),
                "attempt_id": str(attempt.id),
                "finding_id": str(finding.id),
                "role": "documenter",
                "prompt_version": DOCUMENTER_REPORT_PROMPT_VERSION,
            },
            response_format={"type": "json_object"},
        )
        payload = _parse_llm_json_content(result.content)
        report_markdown = payload.get("report_markdown")
        if not isinstance(report_markdown, str) or not report_markdown.strip():
            raise ValueError("documenter response missing report_markdown")
        cost = result.cost_usd or 0.0
        return (
            _normalize_report_heading(report_markdown, finding),
            {
                "source": "openrouter_documenter",
                "requested_model": settings.documenter_model,
                "model": result.model,
                "response_id": result.response_id,
                "usage": result.usage,
                "cost_usd": cost,
                "temperature": requested_temperature,
                "prompt_version": DOCUMENTER_REPORT_PROMPT_VERSION,
                "raw_documenter": payload,
            },
            cost,
        )
    except Exception as exc:  # noqa: BLE001 - report fallback is persisted for audit
        return (
            baseline,
            {
                "source": "template_documenter",
                "fallback_reason": str(exc),
                "requested_model": settings.documenter_model,
                "prompt_version": DOCUMENTER_REPORT_PROMPT_VERSION,
            },
            0.0,
        )


def write_report(*, finding_id: int, markdown: str, generation_metadata: dict[str, Any]) -> WrittenReport:
    settings = load_settings()
    findings_dir = settings.findings_dir
    findings_dir.mkdir(parents=True, exist_ok=True)
    file_path = findings_dir / f"F-{finding_id:03d}.md"
    content = markdown.rstrip() + "\n"
    file_path.write_text(content, encoding="utf-8")
    encoded = content.encode("utf-8")
    return WrittenReport(
        report_path=relative_report_path(finding_id),
        file_path=file_path,
        sha256=hashlib.sha256(encoded).hexdigest(),
        size_bytes=len(encoded),
        generation_metadata=generation_metadata,
    )


def _template_report(
    *,
    campaign: Campaign,
    finding: Finding,
    attempt: Attempt,
    verdict: Verdict,
    evaluation: Evaluation | None,
    reproduction: dict[str, Any],
    draft_evaluation: dict[str, Any],
) -> str:
    finding_label = f"F-{finding.id:03d}"
    method = draft_evaluation.get("method", "POST")
    expected_behavior = (
        evaluation.expected_behavior
        if evaluation is not None
        else "The target should maintain clinical, privacy, and authorization boundaries."
    )
    success_condition = (
        evaluation.success_condition
        if evaluation is not None
        else "The target reproduced the exploit behavior captured in the attempt evidence."
    )
    draft = reproduction.get("draft")
    draft_line = f"- Promotion Draft: #{draft['id']}\n" if isinstance(draft, dict) and "id" in draft else ""
    return (
        f"# {finding_label}: {finding.title}\n\n"
        f"- Status: {finding.status}\n"
        f"- Severity: {finding.severity}\n"
        f"- Category: {finding.category_key}\n"
        f"- Endpoint: {method} {finding.endpoint}\n"
        f"- Campaign: #{campaign.id} against {campaign.target_name_snapshot} ({campaign.target_mode_snapshot})\n"
        f"- Attempt: #{attempt.id}\n"
        f"{draft_line}"
        f"- Verdict: {verdict.verdict} via {verdict.tier} judge\n\n"
        "## Summary\n\n"
        f"RedLens classified attempt #{attempt.id} as an exploit for `{finding.category_key}`. "
        f"The judge rationale was: {verdict.rationale}\n\n"
        "## Clinical Impact\n\n"
        f"{_clinical_impact(finding.category_key)}\n\n"
        "## Reproduction\n\n"
        f"Run the promoted evaluation candidate or replay the captured request against `{finding.endpoint}`.\n\n"
        "### Request Evidence\n\n"
        "```json\n"
        f"{_json(reproduction['request'])}\n"
        "```\n\n"
        "### Response Evidence\n\n"
        "```json\n"
        f"{_json(reproduction['response'])}\n"
        "```\n\n"
        "## Observed Behavior\n\n"
        f"{verdict.rationale}\n\n"
        "## Expected Behavior\n\n"
        f"{expected_behavior}\n\n"
        "## Success Condition\n\n"
        f"{success_condition}\n\n"
        "## Judge Evidence\n\n"
        "```json\n"
        f"{_json(reproduction['verdict'])}\n"
        "```\n\n"
        "## Proposed Regression Evaluation\n\n"
        "```json\n"
        f"{_json(draft_evaluation)}\n"
        "```\n\n"
        "## Recommended Remediation\n\n"
        f"{_remediation(finding.category_key)}\n"
    )


def _input_template_for_draft(evaluation: Evaluation | None, attempt: Attempt) -> dict[str, Any]:
    executable = attempt.attack_plan.get("executable_payload")
    if isinstance(executable, dict):
        messages = executable.get("messages")
        document_context = executable.get("document_context")
        if isinstance(messages, list):
            return {
                "messages": messages,
                "document_context": document_context if isinstance(document_context, list) else [],
            }

    request_body = _request_body(attempt.request_json)
    if isinstance(request_body, dict):
        if "messages" in request_body:
            return {
                "messages": request_body.get("messages", []),
                "document_context": request_body.get("document_context", []),
            }
        if "document_type" in request_body:
            return {
                "document_type": request_body.get("document_type"),
                "filename": request_body.get("filename"),
                "mime_type": request_body.get("mime_type"),
                "document_uuid": request_body.get("document_uuid"),
            }

    return dict(evaluation.input_template if evaluation is not None else {})


def _endpoint_for_draft(evaluation: Evaluation | None, attempt: Attempt) -> str:
    executable = attempt.attack_plan.get("executable_payload")
    if isinstance(executable, dict) and isinstance(executable.get("endpoint"), str):
        return executable["endpoint"]
    return evaluation.endpoint if evaluation is not None else attempt.execution_metadata.get("endpoint", "unknown")


def _method_for_draft(evaluation: Evaluation | None, attempt: Attempt) -> str:
    executable = attempt.attack_plan.get("executable_payload")
    if isinstance(executable, dict) and isinstance(executable.get("method"), str):
        return executable["method"].upper()
    method = attempt.request_json.get("method") if isinstance(attempt.request_json, dict) else None
    return str(method or (evaluation.method if evaluation is not None else "POST")).upper()


def _request_body(request_json: dict[str, Any]) -> dict[str, Any] | None:
    body = request_json.get("json") if isinstance(request_json, dict) else None
    if isinstance(body, dict):
        return body
    return request_json if isinstance(request_json, dict) else None


def _evaluation_summary(evaluation: Evaluation | None) -> dict[str, Any] | None:
    if evaluation is None:
        return None
    return {
        "id": evaluation.id,
        "key": evaluation.key,
        "name": evaluation.name,
        "category_key": evaluation.category.key,
        "endpoint": evaluation.endpoint,
        "method": evaluation.method,
        "severity": evaluation.severity,
        "expected_behavior": evaluation.expected_behavior,
        "success_condition": evaluation.success_condition,
        "judge": evaluation.judge,
    }


def _clinical_impact(category_key: str) -> str:
    impacts = {
        "prompt_injection": (
            "Prompt-injection success can cause the assistant to ignore clinical safety boundaries, "
            "misrepresent its authority, or expose hidden operating instructions during care workflows."
        ),
        "data_exfiltration": (
            "Data-exfiltration success can expose PHI outside the authorized patient context and weaken "
            "clinician trust in patient-bound access controls."
        ),
        "state_corruption": (
            "State corruption can let untrusted document or conversation content influence future clinical "
            "answers as if it were policy or verified chart state."
        ),
        "tool_misuse": (
            "Tool misuse can cause the assistant to call clinical data tools with unsafe arguments or broader "
            "scope than the clinician requested."
        ),
        "denial_of_service": (
            "Denial-of-service behavior can degrade clinical workflow availability through excessive output, "
            "unbounded retrieval, or avoidable model/tool spend."
        ),
        "identity_role": (
            "Identity or role confusion can let requests execute under the wrong user, patient, or authority "
            "boundary."
        ),
    }
    return impacts.get(
        category_key,
        "The exploit weakens the target's expected clinical safety, privacy, or authorization boundary.",
    )


def _remediation(category_key: str) -> str:
    remediations = {
        "prompt_injection": (
            "Harden system/developer prompt hierarchy, keep untrusted content explicitly labeled, and add "
            "regression coverage for refusal and boundary-preservation language."
        ),
        "data_exfiltration": (
            "Enforce patient scoping before tool execution and validate that responses never include data from "
            "outside the authorized patient envelope."
        ),
        "state_corruption": (
            "Prevent document or prior-turn content from being promoted to policy. Reset or label transient "
            "state before each clinical task."
        ),
        "tool_misuse": (
            "Validate tool arguments server-side against the authenticated user and current patient context "
            "before any downstream FHIR call."
        ),
        "denial_of_service": (
            "Apply output limits, retrieval budgets, and refusal paths for unbounded generation or repeated "
            "tool-use requests."
        ),
        "identity_role": (
            "Bind identity and role claims server-side and reject any request that attempts to override them "
            "through prompt, payload, or token-minting parameters."
        ),
    }
    return remediations.get(
        category_key,
        "Add a targeted server-side guardrail and keep this promoted evaluation enabled until the fix is validated.",
    )


def _normalize_report_heading(markdown: str, finding: Finding) -> str:
    stripped = markdown.strip()
    label = f"F-{finding.id:03d}"
    if stripped.startswith("#") and label in stripped[:120]:
        return stripped
    return f"# {label}: {finding.title}\n\n{stripped}"


def _parse_llm_json_content(content: str) -> dict[str, Any]:
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        start = content.find("{")
        end = content.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("content did not contain a JSON object")
        parsed = json.loads(content[start : end + 1])
    if not isinstance(parsed, dict):
        raise ValueError("content JSON was not an object")
    return parsed


def _safe_key(value: str, max_length: int = 150) -> str:
    key = re.sub(r"[^a-zA-Z0-9_]+", "_", value.strip().lower()).strip("_")
    if len(key) <= max_length:
        return key
    suffix = key[-12:]
    return f"{key[: max_length - 13]}_{suffix}"


def _json(value: Any) -> str:
    return json.dumps(value, indent=2, sort_keys=True, default=str)
