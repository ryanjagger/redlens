"""FastAPI routes."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.agents.campaign_graph import DeterministicCampaignExecutor
from app.config import load_settings
from app.database import get_db
from app.models import (
    Artifact,
    Attempt,
    Campaign,
    Evaluation,
    EvaluationResult,
    EvaluationRun,
    Finding,
    PromotedEvalDraft,
    Target,
    ThreatCategory,
)
from app.runner import EvaluationRunner
from app.schemas import (
    CampaignCreate,
    CampaignDetail,
    CampaignRead,
    DraftReviewUpdate,
    EvaluationRead,
    FindingReportRead,
    FindingRead,
    ResultRead,
    RunCreate,
    RunDetail,
    RunSummary,
    TargetCreate,
    TargetRead,
    ThreatCategoryRead,
    PromotedEvalDraftRead,
)

router = APIRouter(prefix="/api")
DbSession = Annotated[Session, Depends(get_db)]


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/targets", response_model=list[TargetRead])
def list_targets(db: DbSession) -> list[Target]:
    return list(db.scalars(select(Target).order_by(Target.id)))


@router.post("/targets", response_model=TargetRead, status_code=status.HTTP_201_CREATED)
def create_target(payload: TargetCreate, db: DbSession) -> Target:
    target = Target(**payload.model_dump())
    db.add(target)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="target name already exists") from exc
    db.refresh(target)
    return target


@router.post("/campaigns", response_model=CampaignRead, status_code=status.HTTP_201_CREATED)
def create_campaign(payload: CampaignCreate, db: DbSession) -> Campaign:
    target = db.get(Target, payload.target_id)
    if target is None:
        raise HTTPException(status_code=404, detail="target not found")

    campaign = Campaign(
        target_id=target.id,
        target_name_snapshot=target.name,
        target_mode_snapshot=target.mode,
        target_base_url_snapshot=target.base_url,
        target_user_uuid_snapshot=target.user_uuid,
        target_patient_uuid_snapshot=target.patient_uuid,
        status="needs_live_approval" if target.mode == "live" else "draft",
        focus_hint=payload.focus_hint,
        llm_mode=payload.llm_mode,
        max_attempts=payload.max_attempts,
        max_wall_clock_seconds=payload.max_wall_clock_seconds,
        max_cost_usd=payload.max_cost_usd,
    )
    db.add(campaign)
    db.commit()
    db.refresh(campaign)
    return campaign


@router.get("/campaigns", response_model=list[CampaignRead])
def list_campaigns(db: DbSession) -> list[Campaign]:
    return list(db.scalars(select(Campaign).order_by(Campaign.id.desc())))


@router.get("/campaigns/{campaign_id}", response_model=CampaignDetail)
def get_campaign(campaign_id: int, db: DbSession) -> Campaign:
    campaign = _campaign_detail_query(db, campaign_id)
    if campaign is None:
        raise HTTPException(status_code=404, detail="campaign not found")
    return campaign


@router.post("/campaigns/{campaign_id}/start", response_model=CampaignRead)
async def start_campaign(campaign_id: int, db: DbSession) -> Campaign:
    campaign = db.get(Campaign, campaign_id)
    if campaign is None:
        raise HTTPException(status_code=404, detail="campaign not found")
    if campaign.target_mode_snapshot == "live" and campaign.live_approved_at is None:
        raise HTTPException(status_code=400, detail="live campaigns must be approved before start")
    return await _start_campaign(db, campaign)


@router.post("/campaigns/{campaign_id}/approve-live", response_model=CampaignRead)
async def approve_live_campaign(campaign_id: int, db: DbSession) -> Campaign:
    campaign = db.get(Campaign, campaign_id)
    if campaign is None:
        raise HTTPException(status_code=404, detail="campaign not found")
    if campaign.target_mode_snapshot != "live":
        raise HTTPException(status_code=400, detail="campaign target is not live")
    if campaign.status not in {"needs_live_approval", "draft"}:
        raise HTTPException(status_code=400, detail=f"campaign cannot be approved from status {campaign.status}")
    now = datetime.now(UTC)
    campaign.live_approved_at = now
    campaign.last_activity_at = now
    return await _start_campaign(db, campaign)


@router.post("/campaigns/{campaign_id}/cancel", response_model=CampaignRead)
def cancel_campaign(campaign_id: int, db: DbSession) -> Campaign:
    campaign = db.get(Campaign, campaign_id)
    if campaign is None:
        raise HTTPException(status_code=404, detail="campaign not found")
    if campaign.status in {"completed", "budget_exhausted", "cancelled", "failed"}:
        raise HTTPException(status_code=400, detail=f"campaign cannot be cancelled from status {campaign.status}")

    now = datetime.now(UTC)
    campaign.status = "cancelled"
    campaign.finished_at = now
    campaign.last_activity_at = now
    campaign.stop_reason = "operator_cancelled"
    db.commit()
    db.refresh(campaign)
    return campaign


@router.get("/threat-categories", response_model=list[ThreatCategoryRead])
def list_threat_categories(db: DbSession) -> list[ThreatCategoryRead]:
    rows = list(db.scalars(select(ThreatCategory).order_by(ThreatCategory.id)))
    latest_run_id = db.scalar(select(func.max(EvaluationRun.id)))
    failed_by_category: dict[str, int] = {}
    if latest_run_id is not None:
        failed_rows = db.execute(
            select(ThreatCategory.key, func.count(EvaluationResult.id))
            .join(Evaluation, Evaluation.category_id == ThreatCategory.id)
            .join(EvaluationResult, EvaluationResult.evaluation_id == Evaluation.id)
            .where(EvaluationResult.run_id == latest_run_id)
            .where(EvaluationResult.status == "failed")
            .group_by(ThreatCategory.key)
        )
        failed_by_category = {key: count for key, count in failed_rows}

    return [
        ThreatCategoryRead(
            id=category.id,
            key=category.key,
            name=category.name,
            description=category.description,
            evaluation_count=len(category.evaluations),
            last_failed_count=failed_by_category.get(category.key, 0),
        )
        for category in rows
    ]


@router.get("/evaluations", response_model=list[EvaluationRead])
def list_evaluations(db: DbSession) -> list[EvaluationRead]:
    evaluations = list(
        db.scalars(select(Evaluation).options(selectinload(Evaluation.category)).order_by(Evaluation.id))
    )
    latest_status_by_eval = _latest_status_by_eval(db)
    return [
        EvaluationRead(
            id=evaluation.id,
            key=evaluation.key,
            name=evaluation.name,
            category_id=evaluation.category_id,
            category_key=evaluation.category.key,
            category_name=evaluation.category.name,
            endpoint=evaluation.endpoint,
            method=evaluation.method,
            severity=evaluation.severity,
            expected_behavior=evaluation.expected_behavior,
            success_condition=evaluation.success_condition,
            enabled=evaluation.enabled,
            last_status=latest_status_by_eval.get(evaluation.id),
        )
        for evaluation in evaluations
    ]


@router.post("/runs", response_model=RunSummary, status_code=status.HTTP_201_CREATED)
async def create_run(payload: RunCreate, db: DbSession) -> RunSummary:
    try:
        run = await EvaluationRunner(db).run(payload.target_id, payload.evaluation_ids)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return _run_summary(run)


@router.get("/runs", response_model=list[RunSummary])
def list_runs(db: DbSession) -> list[RunSummary]:
    runs = list(
        db.scalars(
            select(EvaluationRun)
            .options(selectinload(EvaluationRun.target))
            .order_by(EvaluationRun.id.desc())
        )
    )
    return [_run_summary(run) for run in runs]


@router.get("/runs/{run_id}", response_model=RunDetail)
def get_run(run_id: int, db: DbSession) -> RunDetail:
    run = db.scalar(
        select(EvaluationRun)
        .where(EvaluationRun.id == run_id)
        .options(
            selectinload(EvaluationRun.target),
            selectinload(EvaluationRun.results)
            .selectinload(EvaluationResult.evaluation)
            .selectinload(Evaluation.category),
            selectinload(EvaluationRun.results)
            .selectinload(EvaluationResult.evaluation)
            .selectinload(Evaluation.accepted_drafts)
            .selectinload(PromotedEvalDraft.finding),
        )
    )
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")

    summary = _run_summary(run)
    return RunDetail(
        **summary.model_dump(),
        results=[
            _result_read(result)
            for result in sorted(run.results, key=lambda item: item.id)
        ],
    )


@router.get("/findings", response_model=list[FindingRead])
def list_findings(db: DbSession) -> list[Finding]:
    return list(db.scalars(select(Finding).order_by(Finding.id.desc())))


@router.get("/findings/{finding_id}/report", response_model=FindingReportRead)
def get_finding_report(finding_id: int, db: DbSession) -> FindingReportRead:
    finding = db.get(Finding, finding_id)
    if finding is None:
        raise HTTPException(status_code=404, detail="finding not found")
    if not finding.report_path:
        raise HTTPException(status_code=404, detail="finding report is not available")

    artifact = db.scalar(
        select(Artifact)
        .where(Artifact.owner_type == "finding")
        .where(Artifact.owner_id == finding.id)
        .where(Artifact.kind == "finding_report")
        .where(Artifact.uri == finding.report_path)
        .order_by(Artifact.id.desc())
    )
    report_file = _finding_report_file(finding.report_path)
    if report_file.exists() and report_file.is_file():
        content = report_file.read_text(encoding="utf-8")
        storage_backend = artifact.storage_backend if artifact is not None else "filesystem"
        mime_type = artifact.mime_type if artifact is not None and artifact.mime_type else "text/markdown"
        redaction_status = artifact.redaction_status if artifact is not None else "unreviewed"
    else:
        content = _legacy_finding_report(finding)
        storage_backend = "db_fallback"
        mime_type = "text/markdown"
        redaction_status = "unreviewed"
    encoded = content.encode("utf-8")
    return FindingReportRead(
        finding_id=finding.id,
        report_path=finding.report_path,
        content=content,
        artifact_id=artifact.id if artifact is not None else None,
        storage_backend=storage_backend,
        sha256=artifact.sha256 if artifact is not None and artifact.sha256 else hashlib.sha256(encoded).hexdigest(),
        mime_type=mime_type,
        size_bytes=artifact.size_bytes if artifact is not None and artifact.size_bytes is not None else len(encoded),
        redaction_status=redaction_status,
        generation_metadata=_finding_report_generation_metadata(finding),
    )


@router.get("/promoted-eval-drafts", response_model=list[PromotedEvalDraftRead])
def list_promoted_eval_drafts(db: DbSession) -> list[PromotedEvalDraft]:
    return list(db.scalars(select(PromotedEvalDraft).order_by(PromotedEvalDraft.id.desc())))


@router.post("/results/{result_id}/promote", response_model=FindingRead, status_code=status.HTTP_201_CREATED)
def promote_result(result_id: int, db: DbSession) -> Finding:
    result = db.scalar(
        select(EvaluationResult)
        .where(EvaluationResult.id == result_id)
        .options(selectinload(EvaluationResult.evaluation).selectinload(Evaluation.category))
    )
    if result is None:
        raise HTTPException(status_code=404, detail="result not found")
    if result.status == "passed":
        raise HTTPException(status_code=400, detail="only failed or errored results can be promoted")

    existing = db.scalar(select(Finding).where(Finding.result_id == result.id))
    if existing is not None:
        return existing

    evaluation = result.evaluation
    reproduction = {
        "run_id": result.run_id,
        "evaluation": evaluation.key,
        "endpoint": evaluation.endpoint,
        "request": result.request_json,
        "response": result.response_json,
        "judge_reason": result.judge_reason,
    }
    finding = Finding(
        result_id=result.id,
        title=evaluation.name,
        severity=evaluation.severity,
        category_key=evaluation.category.key,
        endpoint=evaluation.endpoint,
        reproduction_steps=json.dumps(reproduction, indent=2, sort_keys=True, default=str),
    )
    db.add(finding)
    db.commit()
    db.refresh(finding)
    return finding


@router.post("/promoted-eval-drafts/{draft_id}/approve", response_model=PromotedEvalDraftRead)
def approve_promoted_eval_draft(
    draft_id: int,
    db: DbSession,
    payload: DraftReviewUpdate | None = None,
) -> PromotedEvalDraft:
    return _accept_promoted_eval_draft(db, draft_id, enabled=True, review_notes=_review_notes(payload))


@router.post("/promoted-eval-drafts/{draft_id}/save-disabled", response_model=PromotedEvalDraftRead)
def save_promoted_eval_draft_disabled(
    draft_id: int,
    db: DbSession,
    payload: DraftReviewUpdate | None = None,
) -> PromotedEvalDraft:
    return _accept_promoted_eval_draft(db, draft_id, enabled=False, review_notes=_review_notes(payload))


@router.post("/promoted-eval-drafts/{draft_id}/reject", response_model=PromotedEvalDraftRead)
def reject_promoted_eval_draft(
    draft_id: int,
    db: DbSession,
    payload: DraftReviewUpdate | None = None,
) -> PromotedEvalDraft:
    return _mark_promoted_eval_draft(db, draft_id, status="rejected", review_notes=_review_notes(payload))


@router.post("/promoted-eval-drafts/{draft_id}/needs-revision", response_model=PromotedEvalDraftRead)
def request_promoted_eval_draft_revision(
    draft_id: int,
    db: DbSession,
    payload: DraftReviewUpdate | None = None,
) -> PromotedEvalDraft:
    return _mark_promoted_eval_draft(db, draft_id, status="needs_revision", review_notes=_review_notes(payload))


def _run_summary(run: EvaluationRun) -> RunSummary:
    return RunSummary(
        id=run.id,
        target_id=run.target_id,
        target_name=run.target.name,
        status=run.status,
        started_at=run.started_at,
        finished_at=run.finished_at,
        total_count=run.total_count,
        passed_count=run.passed_count,
        failed_count=run.failed_count,
        error_count=run.error_count,
    )


def _result_read(result: EvaluationResult) -> ResultRead:
    origin = _promoted_origin(result.evaluation)
    return ResultRead(
        id=result.id,
        run_id=result.run_id,
        evaluation_id=result.evaluation_id,
        evaluation_key=result.evaluation.key,
        evaluation_name=result.evaluation.name,
        category_key=result.evaluation.category.key,
        category_name=result.evaluation.category.name,
        endpoint=result.evaluation.endpoint,
        severity=result.evaluation.severity,
        status=result.status,
        request_json=result.request_json,
        response_json=result.response_json,
        status_code=result.status_code,
        latency_ms=result.latency_ms,
        judge_name=result.judge_name,
        judge_reason=result.judge_reason,
        origin_finding_id=origin.finding_id if origin is not None else None,
        origin_finding_status=origin.finding.status if origin is not None else None,
        origin_report_path=origin.finding.report_path if origin is not None else None,
        origin_draft_id=origin.id if origin is not None else None,
        created_at=result.created_at,
    )


def _promoted_origin(evaluation: Evaluation) -> PromotedEvalDraft | None:
    drafts = sorted(
        (draft for draft in evaluation.accepted_drafts if draft.finding is not None),
        key=lambda draft: draft.id,
    )
    return drafts[0] if drafts else None


def _latest_status_by_eval(db: Session) -> dict[int, str]:
    latest_run_id = db.scalar(select(func.max(EvaluationRun.id)))
    if latest_run_id is None:
        return {}
    rows = db.execute(
        select(EvaluationResult.evaluation_id, EvaluationResult.status).where(
            EvaluationResult.run_id == latest_run_id
        )
    )
    return {evaluation_id: status for evaluation_id, status in rows}


async def _start_campaign(db: Session, campaign: Campaign) -> Campaign:
    if campaign.status == "running":
        return campaign
    if campaign.status not in {"draft", "needs_live_approval"}:
        raise HTTPException(status_code=400, detail=f"campaign cannot be started from status {campaign.status}")
    _ensure_llm_campaign_configured(campaign)
    if campaign.target_mode_snapshot == "live":
        _ensure_no_running_live_campaign(db, campaign)

    now = datetime.now(UTC)
    campaign.status = "running"
    campaign.started_at = campaign.started_at or now
    campaign.last_activity_at = now
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="another live campaign is already running for this target",
        ) from exc
    db.refresh(campaign)
    try:
        campaign = await DeterministicCampaignExecutor(db).run(campaign.id)
    except ValueError as exc:
        campaign.status = "failed"
        campaign.stop_reason = str(exc)
        campaign.finished_at = datetime.now(UTC)
        campaign.last_activity_at = campaign.finished_at
        db.commit()
        db.refresh(campaign)
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return campaign


def _ensure_llm_campaign_configured(campaign: Campaign) -> None:
    if campaign.llm_mode != "llm_assisted":
        return
    settings = load_settings()
    missing = []
    if not settings.openrouter_api_key:
        missing.append("OPENROUTER_API_KEY")
    if not settings.red_team_model:
        missing.append("REDLENS_RED_TEAM_MODEL")
    if missing:
        raise HTTPException(
            status_code=400,
            detail=f"llm_assisted campaigns require {', '.join(missing)}",
        )


def _ensure_no_running_live_campaign(db: Session, campaign: Campaign) -> None:
    existing = db.scalar(
        select(Campaign.id)
        .where(Campaign.id != campaign.id)
        .where(Campaign.target_id == campaign.target_id)
        .where(Campaign.target_mode_snapshot == "live")
        .where(Campaign.status == "running")
    )
    if existing is not None:
        raise HTTPException(
            status_code=409,
            detail="another live campaign is already running for this target",
        )


def _campaign_detail_query(db: Session, campaign_id: int) -> Campaign | None:
    return db.scalar(
        select(Campaign)
        .where(Campaign.id == campaign_id)
        .options(
            selectinload(Campaign.attempts).selectinload(Attempt.verdicts),
            selectinload(Campaign.attempts).selectinload(Attempt.promoted_eval_drafts),
        )
    )


def _accept_promoted_eval_draft(
    db: Session,
    draft_id: int,
    *,
    enabled: bool,
    review_notes: str | None,
) -> PromotedEvalDraft:
    draft = _load_promoted_eval_draft(db, draft_id)
    if draft.status not in {"pending", "needs_revision"}:
        raise HTTPException(status_code=400, detail=f"draft cannot be accepted from status {draft.status}")

    evaluation = _evaluation_from_draft(db, draft, enabled=enabled)
    db.add(evaluation)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="draft evaluation key already exists") from exc

    now = datetime.now(UTC)
    draft.status = "accepted" if enabled else "saved_disabled"
    draft.review_notes = review_notes
    draft.reviewed_at = now
    draft.accepted_evaluation_id = evaluation.id
    draft.finding.linked_evaluation_id = evaluation.id
    if draft.finding.report_path is None:
        draft.finding.report_path = draft.report_path
    db.commit()
    db.refresh(draft)
    return draft


def _mark_promoted_eval_draft(
    db: Session,
    draft_id: int,
    *,
    status: str,
    review_notes: str | None,
) -> PromotedEvalDraft:
    draft = _load_promoted_eval_draft(db, draft_id)
    if draft.status not in {"pending", "needs_revision"}:
        raise HTTPException(status_code=400, detail=f"draft cannot be changed from status {draft.status}")
    draft.status = status
    draft.review_notes = review_notes
    draft.reviewed_at = datetime.now(UTC)
    db.commit()
    db.refresh(draft)
    return draft


def _finding_report_file(report_path: str) -> Path:
    filename = Path(report_path).name
    if not filename or filename in {".", ".."}:
        raise HTTPException(status_code=400, detail="invalid finding report path")
    return load_settings().findings_dir / filename


def _finding_report_generation_metadata(finding: Finding) -> dict[str, Any]:
    try:
        reproduction = json.loads(finding.reproduction_steps)
    except json.JSONDecodeError:
        return {}
    if not isinstance(reproduction, dict):
        return {}
    report = reproduction.get("report")
    if not isinstance(report, dict):
        return {}
    documenter = report.get("documenter")
    return documenter if isinstance(documenter, dict) else {}


def _legacy_finding_report(finding: Finding) -> str:
    return (
        f"# F-{finding.id:03d}: {finding.title}\n\n"
        f"- Status: {finding.status}\n"
        f"- Severity: {finding.severity}\n"
        f"- Category: {finding.category_key}\n"
        f"- Endpoint: {finding.endpoint}\n"
        "- Source: legacy DB fallback; markdown report file was not found\n\n"
        "## Reproduction Evidence\n\n"
        "```json\n"
        f"{finding.reproduction_steps}\n"
        "```\n"
    )


def _load_promoted_eval_draft(db: Session, draft_id: int) -> PromotedEvalDraft:
    draft = db.scalar(
        select(PromotedEvalDraft)
        .where(PromotedEvalDraft.id == draft_id)
        .options(selectinload(PromotedEvalDraft.finding))
    )
    if draft is None:
        raise HTTPException(status_code=404, detail="promoted eval draft not found")
    return draft


def _evaluation_from_draft(db: Session, draft: PromotedEvalDraft, *, enabled: bool) -> Evaluation:
    data = dict(draft.evaluation_json)
    category_id = _draft_category_id(db, data)
    try:
        key = str(data["key"])
        name = str(data["name"])
        endpoint = str(data["endpoint"])
    except KeyError as exc:
        raise HTTPException(status_code=400, detail=f"draft evaluation is missing {exc.args[0]}") from exc

    return Evaluation(
        key=key,
        name=name,
        category_id=category_id,
        endpoint=endpoint,
        method=str(data.get("method", "POST")),
        severity=str(data.get("severity", draft.finding.severity)),
        input_template=dict(data.get("input_template", {})),
        expected_behavior=str(data.get("expected_behavior", "")),
        success_condition=str(data.get("success_condition", "")),
        judge=dict(data.get("judge", {})),
        enabled=enabled,
    )


def _draft_category_id(db: Session, data: dict[str, object]) -> int:
    category_id = data.get("category_id")
    if isinstance(category_id, int):
        category = db.get(ThreatCategory, category_id)
        if category is not None:
            return category.id

    category_key = data.get("category_key")
    if isinstance(category_key, str):
        category = db.scalar(select(ThreatCategory).where(ThreatCategory.key == category_key))
        if category is not None:
            return category.id

    raise HTTPException(status_code=400, detail="draft evaluation is missing a valid category")


def _review_notes(payload: DraftReviewUpdate | None) -> str | None:
    if payload is None:
        return None
    return payload.review_notes
