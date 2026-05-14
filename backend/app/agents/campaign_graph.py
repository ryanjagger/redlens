"""Deterministic LangGraph skeleton for exploration campaigns."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any, Literal, TypedDict

from langgraph.graph import END, StateGraph
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.adapters import adapter_for
from app.judges import judge_response
from app.models import Attempt, Campaign, Evaluation, Finding, PromotedEvalDraft, Target, Verdict


class CampaignGraphState(TypedDict):
    campaign_id: int
    attempts_run: int
    last_attempt_id: int | None
    last_verdict: str | None
    should_continue: bool
    stop_reason: str | None


class DeterministicCampaignExecutor:
    """Run the v1 exploration loop through LangGraph with deterministic nodes."""

    def __init__(self, db: Session) -> None:
        self.db = db
        builder = StateGraph(CampaignGraphState)
        builder.add_node("prepare_campaign", self._prepare_campaign)
        builder.add_node("run_red_team_attempt", self._run_red_team_attempt)
        builder.add_node("judge_attempt", self._judge_attempt)
        builder.add_node("document_if_exploit", self._document_if_exploit)
        builder.add_node("decide_continue", self._decide_continue)
        builder.set_entry_point("prepare_campaign")
        builder.add_conditional_edges(
            "prepare_campaign",
            self._route_after_prepare,
            {"continue": "run_red_team_attempt", "stop": END},
        )
        builder.add_edge("run_red_team_attempt", "judge_attempt")
        builder.add_edge("judge_attempt", "document_if_exploit")
        builder.add_edge("document_if_exploit", "decide_continue")
        builder.add_conditional_edges(
            "decide_continue",
            self._route_after_decision,
            {"continue": "run_red_team_attempt", "stop": END},
        )
        self._graph = builder.compile()

    async def run(self, campaign_id: int) -> Campaign:
        await self._graph.ainvoke(
            {
                "campaign_id": campaign_id,
                "attempts_run": 0,
                "last_attempt_id": None,
                "last_verdict": None,
                "should_continue": True,
                "stop_reason": None,
            }
        )
        campaign = self._load_campaign(campaign_id)
        self.db.refresh(campaign)
        return campaign

    def _prepare_campaign(self, state: CampaignGraphState) -> dict[str, Any]:
        campaign = self._load_campaign(state["campaign_id"])
        if campaign.target_mode_snapshot != "mock":
            now = datetime.now(UTC)
            campaign.status = "running"
            campaign.last_activity_at = now
            campaign.stop_reason = "live_execution_not_enabled"
            self.db.commit()
            return {"should_continue": False, "stop_reason": campaign.stop_reason}

        now = datetime.now(UTC)
        campaign.status = "running"
        campaign.started_at = campaign.started_at or now
        campaign.last_activity_at = now
        self.db.commit()
        return {"should_continue": campaign.max_attempts > 0, "stop_reason": None}

    async def _run_red_team_attempt(self, state: CampaignGraphState) -> dict[str, Any]:
        campaign = self._load_campaign(state["campaign_id"])
        target = self._load_target(campaign.target_id)
        evaluation = self._select_evaluation(campaign, state["attempts_run"])
        now = datetime.now(UTC)
        attempt = Attempt(
            campaign_id=campaign.id,
            target_id=target.id,
            status="executing",
            focus_area=evaluation.category.key,
            vector_key=evaluation.key,
            attack_plan={
                "source": "seeded_evaluation",
                "evaluation_id": evaluation.id,
                "evaluation_key": evaluation.key,
                "name": evaluation.name,
                "endpoint": evaluation.endpoint,
                "expected_behavior": evaluation.expected_behavior,
                "success_condition": evaluation.success_condition,
            },
            transcript={
                "turns": [
                    {
                        "role": "red_team",
                        "content": "Deterministic seed attack selected from the regression suite.",
                    }
                ]
            },
            request_json={},
            response_json={},
            execution_metadata={"agent": "red_team", "mode": "deterministic"},
            started_at=now,
        )
        self.db.add(attempt)
        self.db.commit()
        self.db.refresh(attempt)

        try:
            adapter_response = await adapter_for(target).execute(target, evaluation)
            attempt.request_json = adapter_response.request_json
            attempt.response_json = adapter_response.response_json
            attempt.execution_metadata = {
                **attempt.execution_metadata,
                "latency_ms": adapter_response.latency_ms,
                "status_code": adapter_response.status_code,
                "evaluation_id": evaluation.id,
            }
            attempt.status = "completed"
        except Exception as exc:  # noqa: BLE001 - persisted for operator evidence
            attempt.status = "error"
            attempt.error_message = str(exc)
            attempt.response_json = {"error": str(exc)}
        attempt.finished_at = datetime.now(UTC)
        campaign.last_activity_at = attempt.finished_at
        self.db.commit()
        return {"last_attempt_id": attempt.id}

    def _judge_attempt(self, state: CampaignGraphState) -> dict[str, Any]:
        attempt = self._load_attempt(state["last_attempt_id"])
        campaign = self._load_campaign(state["campaign_id"])
        evaluation_id = attempt.execution_metadata.get("evaluation_id")
        evaluation = self.db.get(Evaluation, evaluation_id) if isinstance(evaluation_id, int) else None

        if attempt.status == "error" or evaluation is None:
            verdict_value = "uncertain"
            severity = "medium"
            confidence = 0.4
            rationale = attempt.error_message or "attempt could not be deterministically evaluated"
        else:
            judged = judge_response(evaluation, attempt.response_json)
            verdict_value = "safe" if judged.passed else "exploit"
            severity = evaluation.severity if not judged.passed else None
            confidence = 0.95
            rationale = judged.reason

        verdict = Verdict(
            attempt_id=attempt.id,
            tier="deterministic",
            verdict=verdict_value,
            severity=severity,
            confidence=confidence,
            rationale=rationale,
            raw_output={
                "attempt_status": attempt.status,
                "evaluation_id": evaluation_id,
                "deterministic_rationale": rationale,
            },
        )
        attempt.status = "judged"
        campaign.last_activity_at = datetime.now(UTC)
        self.db.add(verdict)
        self.db.commit()
        self.db.refresh(verdict)
        return {"last_verdict": verdict.verdict}

    def _document_if_exploit(self, state: CampaignGraphState) -> dict[str, Any]:
        if state["last_verdict"] != "exploit":
            return {}

        attempt = self._load_attempt(state["last_attempt_id"])
        verdict = self.db.scalar(select(Verdict).where(Verdict.attempt_id == attempt.id).order_by(Verdict.id.desc()))
        if verdict is None:
            return {}

        campaign = self._load_campaign(state["campaign_id"])
        evaluation_id = attempt.execution_metadata.get("evaluation_id")
        evaluation = self.db.get(Evaluation, evaluation_id) if isinstance(evaluation_id, int) else None
        title = evaluation.name if evaluation is not None else f"Exploration attempt {attempt.id}"
        category_key = evaluation.category.key if evaluation is not None else attempt.focus_area
        endpoint = evaluation.endpoint if evaluation is not None else "unknown"
        severity = verdict.severity or "medium"
        report_path = f"docs/findings/F-{attempt.id:03d}.md"
        reproduction = {
            "campaign_id": campaign.id,
            "attempt_id": attempt.id,
            "vector_key": attempt.vector_key,
            "request": attempt.request_json,
            "response": attempt.response_json,
            "verdict": verdict.verdict,
            "rationale": verdict.rationale,
        }
        finding = Finding(
            result_id=None,
            title=title,
            severity=severity,
            category_key=category_key,
            endpoint=endpoint,
            status="open",
            reproduction_steps=json.dumps(reproduction, indent=2, sort_keys=True, default=str),
            linked_attempt_id=attempt.id,
            report_path=report_path,
        )
        self.db.add(finding)
        self.db.flush()

        draft = PromotedEvalDraft(
            finding_id=finding.id,
            attempt_id=attempt.id,
            verdict_id=verdict.id,
            status="pending",
            evaluation_json=self._draft_evaluation_json(evaluation, finding),
            report_path=report_path,
        )
        attempt.status = "documented"
        campaign.exploit_count += 1
        campaign.last_activity_at = datetime.now(UTC)
        self.db.add(draft)
        self.db.commit()
        return {}

    def _decide_continue(self, state: CampaignGraphState) -> dict[str, Any]:
        campaign = self._load_campaign(state["campaign_id"])
        attempts_run = state["attempts_run"] + 1
        campaign.attempt_count = attempts_run
        campaign.last_activity_at = datetime.now(UTC)

        if attempts_run >= campaign.max_attempts:
            campaign.status = "completed"
            campaign.stop_reason = "max_attempts_reached"
            campaign.finished_at = campaign.last_activity_at
            campaign.summary = (
                f"Deterministic mock campaign completed {attempts_run} attempts; "
                f"{campaign.exploit_count} exploit verdicts."
            )
            self.db.commit()
            return {
                "attempts_run": attempts_run,
                "should_continue": False,
                "stop_reason": campaign.stop_reason,
            }

        self.db.commit()
        return {"attempts_run": attempts_run, "should_continue": True, "stop_reason": None}

    @staticmethod
    def _route_after_prepare(state: CampaignGraphState) -> Literal["continue", "stop"]:
        return "continue" if state["should_continue"] else "stop"

    @staticmethod
    def _route_after_decision(state: CampaignGraphState) -> Literal["continue", "stop"]:
        return "continue" if state["should_continue"] else "stop"

    def _load_campaign(self, campaign_id: int) -> Campaign:
        campaign = self.db.get(Campaign, campaign_id)
        if campaign is None:
            raise ValueError(f"campaign {campaign_id} was not found")
        return campaign

    def _load_target(self, target_id: int) -> Target:
        target = self.db.get(Target, target_id)
        if target is None:
            raise ValueError(f"target {target_id} was not found")
        return target

    def _load_attempt(self, attempt_id: int | None) -> Attempt:
        if attempt_id is None:
            raise ValueError("attempt id was not set")
        attempt = self.db.get(Attempt, attempt_id)
        if attempt is None:
            raise ValueError(f"attempt {attempt_id} was not found")
        return attempt

    def _select_evaluation(self, campaign: Campaign, offset: int) -> Evaluation:
        stmt = (
            select(Evaluation)
            .options(selectinload(Evaluation.category))
            .where(Evaluation.enabled.is_(True))
            .order_by(Evaluation.id)
        )
        if campaign.focus_hint:
            focused = list(
                self.db.scalars(
                    stmt.join(Evaluation.category).where(
                        (Evaluation.key == campaign.focus_hint)
                        | (Evaluation.name.ilike(f"%{campaign.focus_hint}%"))
                    )
                )
            )
            if focused:
                return focused[offset % len(focused)]

        evaluations = list(self.db.scalars(stmt))
        if not evaluations:
            raise ValueError("no enabled evaluations are available for deterministic campaign execution")
        return evaluations[offset % len(evaluations)]

    @staticmethod
    def _draft_evaluation_json(evaluation: Evaluation | None, finding: Finding) -> dict[str, Any]:
        if evaluation is None:
            return {
                "key": f"draft_finding_{finding.id}",
                "name": finding.title,
                "endpoint": finding.endpoint,
                "severity": finding.severity,
                "enabled": True,
                "linked_finding_id": finding.id,
            }
        return {
            "key": f"promoted_{evaluation.key}_f{finding.id}",
            "name": evaluation.name,
            "category_id": evaluation.category_id,
            "endpoint": evaluation.endpoint,
            "method": evaluation.method,
            "severity": evaluation.severity,
            "input_template": evaluation.input_template,
            "expected_behavior": evaluation.expected_behavior,
            "success_condition": evaluation.success_condition,
            "judge": evaluation.judge,
            "enabled": True,
            "linked_finding_id": finding.id,
        }
