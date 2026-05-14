"""Campaign report generation for exploration campaigns."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.config import load_settings
from app.models import Attempt, Campaign, Finding, PromotedEvalDraft, Verdict

REPORT_PATH_PREFIX = "docs/campaigns"
REPORT_VERSION = "campaign_report_v1"


@dataclass(frozen=True)
class WrittenCampaignReport:
    report_path: str
    file_path: Path
    sha256: str
    size_bytes: int
    generation_metadata: dict[str, Any]


def relative_campaign_report_path(campaign_id: int) -> str:
    return f"{REPORT_PATH_PREFIX}/C-{campaign_id:03d}.md"


def render_campaign_report_markdown(campaign: Campaign) -> tuple[str, dict[str, Any]]:
    attempts = sorted(campaign.attempts, key=lambda attempt: attempt.id)
    verdicts = [_latest_verdict(attempt) for attempt in attempts]
    verdict_counts = Counter(verdict.verdict for verdict in verdicts if verdict is not None)
    severity_counts = Counter(verdict.severity for verdict in verdicts if verdict is not None and verdict.severity)
    exploit_attempts = [
        attempt
        for attempt in attempts
        if (latest := _latest_verdict(attempt)) is not None and latest.verdict == "exploit"
    ]
    promoted_drafts = [draft for attempt in attempts for draft in attempt.promoted_eval_drafts]
    linked_findings = [finding for attempt in attempts for finding in attempt.findings]
    trace_url = _trace_url(campaign.langfuse)

    lines = [
        f"# Campaign #{campaign.id}: {campaign.target_name_snapshot}",
        "",
        "## Executive Summary",
        "",
        _executive_summary(campaign, attempts, verdict_counts),
        "",
        "## Scope",
        "",
        f"- Target: {campaign.target_name_snapshot}",
        f"- Target mode: {campaign.target_mode_snapshot}",
        f"- Base URL: {campaign.target_base_url_snapshot}",
        f"- User UUID: {_configured_label(campaign.target_user_uuid_snapshot)}",
        f"- Patient UUID: {_configured_label(campaign.target_patient_uuid_snapshot)}",
        f"- LLM mode: {campaign.llm_mode}",
        f"- Focus hint: {campaign.focus_hint or 'none'}",
        f"- Status: {campaign.status}",
        f"- Stop reason: {campaign.stop_reason or 'none'}",
        "",
        "## Verdict Summary",
        "",
        "| Verdict | Count |",
        "| --- | ---: |",
        f"| Exploit | {verdict_counts.get('exploit', 0)} |",
        f"| Safe | {verdict_counts.get('safe', 0)} |",
        f"| Uncertain | {verdict_counts.get('uncertain', 0)} |",
        "",
        "## Cost Analysis",
        "",
        f"- Spend: ${campaign.spent_cost_usd:.4f}",
        f"- Budget: ${campaign.max_cost_usd:.4f}",
        f"- Budget used: {_budget_used(campaign)}",
        f"- Attempts: {campaign.attempt_count} / {campaign.max_attempts}",
        f"- Wall clock: {_duration_seconds(campaign)}s / {campaign.max_wall_clock_seconds}s",
        "",
        "## Attempt Evidence",
        "",
        "| Attempt | Focus | Vector | Status | Verdict | Severity | LLM Cost |",
        "| ---: | --- | --- | --- | --- | --- | ---: |",
        *[_attempt_row(attempt) for attempt in attempts],
        "",
        "## Findings and Promotions",
        "",
        *_finding_lines(linked_findings, promoted_drafts),
        "",
        "## Recommendations",
        "",
        *_recommendation_lines(campaign, exploit_attempts, promoted_drafts),
        "",
        "## Observability",
        "",
        f"- Campaign trace: {trace_url or 'not captured'}",
    ]
    if severity_counts:
        lines.append(
            "- Severity counts: "
            + ", ".join(f"{severity}: {count}" for severity, count in sorted(severity_counts.items()))
        )

    metadata = {
        "source": "template_campaign_report",
        "report_version": REPORT_VERSION,
        "campaign_id": campaign.id,
        "campaign_status": campaign.status,
        "attempt_count": len(attempts),
        "verdict_counts": dict(verdict_counts),
        "generated_at": datetime.now(UTC).isoformat(),
        **({"langfuse": campaign.langfuse} if campaign.langfuse else {}),
    }
    return "\n".join(lines).rstrip() + "\n", metadata


def write_campaign_report(
    *,
    campaign_id: int,
    markdown: str,
    generation_metadata: dict[str, Any],
) -> WrittenCampaignReport:
    settings = load_settings()
    reports_dir = settings.campaign_reports_dir
    reports_dir.mkdir(parents=True, exist_ok=True)
    file_path = reports_dir / f"C-{campaign_id:03d}.md"
    content = markdown.rstrip() + "\n"
    file_path.write_text(content, encoding="utf-8")
    encoded = content.encode("utf-8")
    return WrittenCampaignReport(
        report_path=relative_campaign_report_path(campaign_id),
        file_path=file_path,
        sha256=hashlib.sha256(encoded).hexdigest(),
        size_bytes=len(encoded),
        generation_metadata=generation_metadata,
    )


def _executive_summary(campaign: Campaign, attempts: list[Attempt], verdict_counts: Counter[str]) -> str:
    exploit_count = verdict_counts.get("exploit", 0)
    uncertain_count = verdict_counts.get("uncertain", 0)
    if not attempts:
        return "This campaign has not recorded any attempts yet."
    if exploit_count:
        return (
            f"This campaign recorded {exploit_count} exploit verdict"
            f"{'' if exploit_count == 1 else 's'} across {len(attempts)} attempts. "
            "Review the promoted findings and convert accepted evidence into regression coverage."
        )
    if uncertain_count:
        return (
            f"This campaign completed {len(attempts)} attempts with {uncertain_count} uncertain verdict"
            f"{'' if uncertain_count == 1 else 's'} and no confirmed exploit."
        )
    return (
        f"This campaign completed {len(attempts)} attempts against {campaign.target_name_snapshot} "
        "with no exploit verdicts."
    )


def _attempt_row(attempt: Attempt) -> str:
    verdict = _latest_verdict(attempt)
    verdict_label = verdict.verdict if verdict is not None else "none"
    severity = verdict.severity if verdict is not None and verdict.severity else "none"
    cost = _attempt_cost(attempt, verdict)
    return (
        f"| {attempt.id} | {_md(attempt.focus_area)} | {_md(attempt.vector_key or 'none')} | "
        f"{_md(attempt.status)} | {_md(verdict_label)} | {_md(severity)} | ${cost:.4f} |"
    )


def _finding_lines(linked_findings: list[Finding], promoted_drafts: list[PromotedEvalDraft]) -> list[str]:
    if not linked_findings and not promoted_drafts:
        return ["- No findings or promotion drafts were created for this campaign."]
    lines: list[str] = []
    for finding in sorted(linked_findings, key=lambda item: item.id):
        lines.append(
            f"- Finding F-{finding.id:03d}: {finding.title} "
            f"({finding.status}, {finding.severity}, {finding.report_path or 'no report'})"
        )
    for draft in sorted(promoted_drafts, key=lambda item: item.id):
        lines.append(
            f"- Draft #{draft.id}: finding F-{draft.finding_id:03d}, "
            f"status {draft.status}, report {draft.report_path or 'none'}"
        )
    return lines


def _recommendation_lines(
    campaign: Campaign,
    exploit_attempts: list[Attempt],
    promoted_drafts: list[PromotedEvalDraft],
) -> list[str]:
    if campaign.status == "running":
        return ["- Let the campaign finish before using this report for release decisions."]
    if exploit_attempts:
        pending = [draft for draft in promoted_drafts if draft.status in {"pending", "needs_revision"}]
        lines = [
            "- Review exploit attempts and confirm whether each finding should become regression coverage.",
            "- Re-run accepted regression evaluations after remediation.",
        ]
        if pending:
            lines.append(f"- Resolve {len(pending)} pending promotion draft{'s' if len(pending) != 1 else ''}.")
        return lines
    if campaign.status in {"completed", "budget_exhausted"}:
        return [
            "- Preserve this report as evidence for the tested target and prompt/model configuration.",
            "- Run regression coverage before release if the target implementation changes.",
        ]
    if campaign.status in {"cancelled", "failed"}:
        return ["- Treat this report as partial evidence and start a new campaign after resolving the stop condition."]
    return ["- Start the campaign to collect evidence."]


def _latest_verdict(attempt: Attempt) -> Verdict | None:
    if not attempt.verdicts:
        return None
    return sorted(attempt.verdicts, key=lambda verdict: verdict.id)[-1]


def _attempt_cost(attempt: Attempt, verdict: Verdict | None) -> float:
    cost = _number_from_record(attempt.execution_metadata, "cost_usd")
    if verdict is not None:
        cost += _number_from_record(verdict.raw_output, "cost_usd")
    for draft in attempt.promoted_eval_drafts:
        documenter = _record_from_draft(draft)
        cost += _number_from_record(documenter, "cost_usd")
    return cost


def _record_from_draft(draft: PromotedEvalDraft) -> dict[str, Any]:
    if draft.finding is None:
        return {}
    try:
        reproduction = json.loads(draft.finding.reproduction_steps)
    except (TypeError, ValueError):
        return {}
    report = reproduction.get("report") if isinstance(reproduction, dict) else None
    documenter = report.get("documenter") if isinstance(report, dict) else None
    return documenter if isinstance(documenter, dict) else {}


def _number_from_record(record: dict[str, Any], key: str) -> float:
    value = record.get(key)
    return float(value) if isinstance(value, (int, float)) else 0.0


def _budget_used(campaign: Campaign) -> str:
    if campaign.max_cost_usd <= 0:
        return "unlimited"
    return f"{(campaign.spent_cost_usd / campaign.max_cost_usd) * 100:.1f}%"


def _duration_seconds(campaign: Campaign) -> int:
    if campaign.started_at is None:
        return 0
    end = campaign.finished_at or datetime.now(UTC)
    return max(int((end - campaign.started_at).total_seconds()), 0)


def _trace_url(value: dict[str, Any] | None) -> str | None:
    if not value:
        return None
    trace_url = value.get("trace_url")
    if isinstance(trace_url, str) and trace_url:
        return trace_url
    host = value.get("host")
    trace_id = value.get("trace_id")
    if isinstance(host, str) and isinstance(trace_id, str) and host and trace_id:
        return f"{host.rstrip('/')}/trace/{trace_id}"
    return None


def _configured_label(value: str | None) -> str:
    return "configured" if value else "not configured"


def _md(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")
