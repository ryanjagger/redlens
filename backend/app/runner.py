"""Evaluation execution service."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.adapters import AdapterExecutionError, adapter_for
from app.judges import judge_response
from app.models import Evaluation, EvaluationResult, EvaluationRun, Target


class EvaluationRunner:
    def __init__(self, db: Session) -> None:
        self.db = db

    async def run(self, target_id: int, evaluation_ids: list[int] | None = None) -> EvaluationRun:
        target = self.db.get(Target, target_id)
        if target is None:
            raise ValueError(f"target {target_id} was not found")

        stmt = select(Evaluation).where(Evaluation.enabled.is_(True)).order_by(Evaluation.id)
        if evaluation_ids:
            stmt = stmt.where(Evaluation.id.in_(evaluation_ids))
        evaluations = list(self.db.scalars(stmt))
        if not evaluations:
            raise ValueError("no enabled evaluations matched the request")

        run = EvaluationRun(target_id=target.id, status="running", started_at=datetime.now(UTC))
        self.db.add(run)
        self.db.commit()
        self.db.refresh(run)

        adapter = adapter_for(target)
        counts = {"passed": 0, "failed": 0, "error": 0}
        for evaluation in evaluations:
            try:
                adapter_response = await adapter.execute(target, evaluation)
                if adapter_response.status_code is not None and adapter_response.status_code >= 400:
                    status = "error"
                    reason = f"target returned HTTP {adapter_response.status_code}"
                else:
                    judged = judge_response(evaluation, adapter_response.response_json)
                    status = "passed" if judged.passed else "failed"
                    reason = judged.reason
                result = EvaluationResult(
                    run_id=run.id,
                    evaluation_id=evaluation.id,
                    status=status,
                    request_json=adapter_response.request_json,
                    response_json=adapter_response.response_json,
                    status_code=adapter_response.status_code,
                    latency_ms=adapter_response.latency_ms,
                    judge_name="deterministic",
                    judge_reason=reason,
                )
            except AdapterExecutionError as exc:
                result = EvaluationResult(
                    run_id=run.id,
                    evaluation_id=evaluation.id,
                    status="error",
                    request_json=exc.request_json,
                    response_json=exc.response_json,
                    status_code=exc.status_code,
                    latency_ms=exc.latency_ms,
                    judge_name="adapter",
                    judge_reason=str(exc),
                )
                status = "error"

            counts[status] += 1
            self.db.add(result)
            self.db.flush()

        run.status = "completed"
        run.finished_at = datetime.now(UTC)
        run.total_count = len(evaluations)
        run.passed_count = counts["passed"]
        run.failed_count = counts["failed"]
        run.error_count = counts["error"]
        self.db.commit()
        self.db.refresh(run)
        return run

