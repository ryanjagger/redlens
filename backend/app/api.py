"""FastAPI routes."""

from __future__ import annotations

import json
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.database import get_db
from app.models import Evaluation, EvaluationResult, EvaluationRun, Finding, Target, ThreatCategory
from app.runner import EvaluationRunner
from app.schemas import (
    EvaluationRead,
    FindingRead,
    ResultRead,
    RunCreate,
    RunDetail,
    RunSummary,
    TargetCreate,
    TargetRead,
    ThreatCategoryRead,
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
        )
    )
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")

    summary = _run_summary(run)
    return RunDetail(
        **summary.model_dump(),
        results=[
            ResultRead(
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
                created_at=result.created_at,
            )
            for result in sorted(run.results, key=lambda item: item.id)
        ],
    )


@router.get("/findings", response_model=list[FindingRead])
def list_findings(db: DbSession) -> list[Finding]:
    return list(db.scalars(select(Finding).order_by(Finding.id.desc())))


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

