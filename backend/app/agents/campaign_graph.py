"""Deterministic LangGraph skeleton for exploration campaigns."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any, Literal, TypedDict

from langgraph.graph import END, StateGraph
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.prompts import (
    LLM_JUDGE_ATTEMPT_PROMPT_VERSION,
    RED_TEAM_ATTACK_PLAN_PROMPT_VERSION,
    llm_judge_attempt_messages,
    red_team_attack_plan_messages,
)
from app.agents.documenter import (
    build_reproduction,
    draft_evaluation_json,
    relative_report_path,
    render_report_markdown,
    write_report,
)
from app.agents.orchestrator import EvaluationSelection, OrchestratorRouter
from app.adapters import ExecutableAttackPayload, adapter_for
from app.config import load_settings
from app.judges import judge_response
from app.llm.langfuse import redlens_langfuse_flush, redlens_langfuse_observation
from app.llm.openrouter import OpenRouterClient
from app.models import Artifact, Attempt, Campaign, Evaluation, Finding, PromotedEvalDraft, Target, Verdict

BUDGET_STOP_FRACTION = 0.9


def _normalize_messages(plan_payload: dict[str, Any]) -> list[dict[str, str]]:
    messages = plan_payload.get("messages")
    if isinstance(messages, list):
        normalized = []
        for message in messages:
            if not isinstance(message, dict):
                continue
            role = message.get("role")
            content = message.get("content")
            if isinstance(role, str) and isinstance(content, str) and content.strip():
                normalized.append({"role": role, "content": content.strip()})
        if normalized:
            return normalized

    for key in ("message", "attack_message", "prompt", "payload"):
        value = plan_payload.get(key)
        if isinstance(value, str) and value.strip():
            return [{"role": "user", "content": value.strip()}]

    return []


class CampaignGraphState(TypedDict):
    campaign_id: int
    attempts_run: int
    selected_evaluation_id: int | None
    routing_metadata: dict[str, Any] | None
    last_attempt_id: int | None
    last_verdict: str | None
    should_continue: bool
    stop_reason: str | None


class DeterministicCampaignExecutor:
    """Run the v1 exploration loop through LangGraph with deterministic nodes."""

    def __init__(self, db: Session) -> None:
        self.db = db
        builder = StateGraph(CampaignGraphState)
        builder.add_node("prepare_campaign", self._trace_node("prepare_campaign", self._prepare_campaign))
        builder.add_node("select_focus", self._trace_node("select_focus", self._select_focus))
        builder.add_node(
            "run_red_team_attempt",
            self._trace_async_node("run_red_team_attempt", self._run_red_team_attempt),
        )
        builder.add_node("judge_attempt", self._trace_async_node("judge_attempt", self._judge_attempt))
        builder.add_node(
            "document_if_exploit",
            self._trace_async_node("document_if_exploit", self._document_if_exploit),
        )
        builder.add_node("decide_continue", self._trace_node("decide_continue", self._decide_continue))
        builder.set_entry_point("prepare_campaign")
        builder.add_conditional_edges(
            "prepare_campaign",
            self._route_after_prepare,
            {"continue": "select_focus", "stop": END},
        )
        builder.add_edge("select_focus", "run_red_team_attempt")
        builder.add_edge("run_red_team_attempt", "judge_attempt")
        builder.add_edge("judge_attempt", "document_if_exploit")
        builder.add_edge("document_if_exploit", "decide_continue")
        builder.add_conditional_edges(
            "decide_continue",
            self._route_after_decision,
            {"continue": "select_focus", "stop": END},
        )
        self._graph = builder.compile()

    async def run(self, campaign_id: int) -> Campaign:
        settings = load_settings()
        initial_state: CampaignGraphState = {
            "campaign_id": campaign_id,
            "attempts_run": 0,
            "selected_evaluation_id": None,
            "routing_metadata": None,
            "last_attempt_id": None,
            "last_verdict": None,
            "should_continue": True,
            "stop_reason": None,
        }
        campaign = self._load_campaign(campaign_id)
        with redlens_langfuse_observation(
            settings=settings,
            name="redlens.campaign",
            metadata=self._campaign_trace_metadata(campaign),
            input_payload={
                "campaign_id": campaign.id,
                "target_id": campaign.target_id,
                "target_mode": campaign.target_mode_snapshot,
                "llm_mode": campaign.llm_mode,
                "max_attempts": campaign.max_attempts,
            },
        ) as trace:
            if trace is not None:
                campaign.langfuse = trace.metadata.as_dict()
                self.db.commit()
                self.db.refresh(campaign)
            try:
                await self._graph.ainvoke(initial_state)
            except Exception as exc:
                if trace is not None:
                    trace.update_error(str(exc), output={"campaign_id": campaign_id})
                redlens_langfuse_flush(settings)
                raise

            campaign = self._load_campaign(campaign_id)
            self.db.refresh(campaign)
            if trace is not None:
                trace.update_output(
                    {
                        "campaign_id": campaign.id,
                        "status": campaign.status,
                        "attempt_count": campaign.attempt_count,
                        "exploit_count": campaign.exploit_count,
                        "stop_reason": campaign.stop_reason,
                        "spent_cost_usd": campaign.spent_cost_usd,
                    }
                )
        redlens_langfuse_flush(settings)
        return campaign

    def _trace_node(
        self,
        node_name: str,
        handler: Callable[[CampaignGraphState], dict[str, Any]],
    ) -> Callable[[CampaignGraphState], dict[str, Any]]:
        def wrapped(state: CampaignGraphState) -> dict[str, Any]:
            with redlens_langfuse_observation(
                settings=load_settings(),
                name=f"redlens.graph.{node_name}",
                metadata=self._node_trace_metadata(state, node_name),
                input_payload=self._node_trace_input(state),
            ) as trace:
                try:
                    result = handler(state)
                except Exception as exc:
                    if trace is not None:
                        trace.update_error(str(exc))
                    raise
                if trace is not None:
                    trace.update_output(result)
                return result

        return wrapped

    def _trace_async_node(
        self,
        node_name: str,
        handler: Callable[[CampaignGraphState], Awaitable[dict[str, Any]]],
    ) -> Callable[[CampaignGraphState], Awaitable[dict[str, Any]]]:
        async def wrapped(state: CampaignGraphState) -> dict[str, Any]:
            with redlens_langfuse_observation(
                settings=load_settings(),
                name=f"redlens.graph.{node_name}",
                metadata=self._node_trace_metadata(state, node_name),
                input_payload=self._node_trace_input(state),
            ) as trace:
                try:
                    result = await handler(state)
                except Exception as exc:
                    if trace is not None:
                        trace.update_error(str(exc))
                    raise
                if trace is not None:
                    trace.update_output(result)
                return result

        return wrapped

    @staticmethod
    def _node_trace_input(state: CampaignGraphState) -> dict[str, Any]:
        return {
            "campaign_id": state["campaign_id"],
            "attempts_run": state["attempts_run"],
            "selected_evaluation_id": state["selected_evaluation_id"],
            "last_attempt_id": state["last_attempt_id"],
            "last_verdict": state["last_verdict"],
            "should_continue": state["should_continue"],
            "stop_reason": state["stop_reason"],
        }

    @staticmethod
    def _node_trace_metadata(state: CampaignGraphState, node_name: str) -> dict[str, str]:
        metadata = {
            "campaign_id": str(state["campaign_id"]),
            "role": "graph",
            "graph_node": node_name,
            "attempts_run": str(state["attempts_run"]),
        }
        for key in ("selected_evaluation_id", "last_attempt_id", "last_verdict", "stop_reason"):
            value = state.get(key)
            if value is not None:
                metadata[key] = str(value)
        return metadata

    @staticmethod
    def _campaign_trace_metadata(campaign: Campaign) -> dict[str, str]:
        return {
            "campaign_id": str(campaign.id),
            "role": "campaign",
            "target_id": str(campaign.target_id),
            "target_mode": campaign.target_mode_snapshot,
            "llm_mode": campaign.llm_mode,
            "status": campaign.status,
        }

    def _prepare_campaign(self, state: CampaignGraphState) -> dict[str, Any]:
        campaign = self._load_campaign(state["campaign_id"])
        if campaign.target_mode_snapshot == "live" and campaign.live_approved_at is None:
            now = datetime.now(UTC)
            campaign.status = "failed"
            campaign.finished_at = now
            campaign.last_activity_at = now
            campaign.stop_reason = "live_approval_required"
            self.db.commit()
            return {"should_continue": False, "stop_reason": campaign.stop_reason}

        now = datetime.now(UTC)
        campaign.status = "running"
        campaign.started_at = campaign.started_at or now
        campaign.last_activity_at = now
        stop_reason = self._budget_stop_reason(campaign, now)
        if stop_reason:
            self._finish_budget_exhausted(campaign, now=now, stop_reason=stop_reason)
            self.db.commit()
            return {"should_continue": False, "stop_reason": campaign.stop_reason}
        self.db.commit()
        return {"should_continue": campaign.max_attempts > 0, "stop_reason": None}

    def _select_focus(self, state: CampaignGraphState) -> dict[str, Any]:
        campaign = self._load_campaign(state["campaign_id"])
        selection = self._select_evaluation(campaign, state["attempts_run"])
        return {
            "selected_evaluation_id": selection.evaluation.id,
            "routing_metadata": {**selection.metadata, "graph_node": "select_focus"},
        }

    async def _run_red_team_attempt(self, state: CampaignGraphState) -> dict[str, Any]:
        campaign = self._load_campaign(state["campaign_id"])
        target = self._load_target(campaign.target_id)
        evaluation = self._load_evaluation(state["selected_evaluation_id"])
        routing_metadata = state["routing_metadata"] or {}
        now = datetime.now(UTC)
        attack_plan, transcript, execution_metadata, plan_error, executable_payload = await self._build_attack_plan(
            campaign=campaign,
            evaluation=evaluation,
        )
        attempt = Attempt(
            campaign_id=campaign.id,
            target_id=target.id,
            status="error" if plan_error else "executing",
            focus_area=evaluation.category.key,
            vector_key=evaluation.key,
            attack_plan=attack_plan,
            transcript=transcript,
            request_json={},
            response_json={},
            execution_metadata={**execution_metadata, "orchestrator": routing_metadata},
            error_message=plan_error,
            started_at=now,
        )
        self.db.add(attempt)
        self.db.commit()
        self.db.refresh(attempt)

        if plan_error:
            attempt.finished_at = datetime.now(UTC)
            campaign.last_activity_at = attempt.finished_at
            self.db.commit()
            return {"last_attempt_id": attempt.id}

        target_langfuse_metadata: dict[str, str] | None = None
        try:
            with redlens_langfuse_observation(
                settings=load_settings(),
                name="redlens.target_execution",
                metadata={
                    "campaign_id": str(campaign.id),
                    "attempt_id": str(attempt.id),
                    "target_id": str(target.id),
                    "evaluation_id": str(evaluation.id),
                    "role": "target_execution",
                    "target_mode": target.mode,
                    "evaluation_key": evaluation.key,
                },
                input_payload={
                    "target_mode": target.mode,
                    "target_name": target.name,
                    "evaluation_key": evaluation.key,
                    "endpoint": evaluation.endpoint,
                    "execution_source": "llm_attack_plan" if executable_payload is not None else "seeded_evaluation",
                },
            ) as target_trace:
                try:
                    adapter = adapter_for(target)
                    if executable_payload is not None:
                        adapter_response = await adapter.execute_attack_plan(target, evaluation, executable_payload)
                        execution_source = "llm_attack_plan"
                    else:
                        adapter_response = await adapter.execute(target, evaluation)
                        execution_source = "seeded_evaluation"
                except Exception as exc:
                    if target_trace is not None:
                        target_trace.update_error(str(exc))
                    raise
                if target_trace is not None:
                    target_trace.update_output(
                        {
                            "execution_source": execution_source,
                            "latency_ms": adapter_response.latency_ms,
                            "status_code": adapter_response.status_code,
                        }
                    )
                    target_langfuse_metadata = target_trace.metadata.as_dict()
            attempt.request_json = adapter_response.request_json
            attempt.response_json = adapter_response.response_json
            attempt.execution_metadata = {
                **attempt.execution_metadata,
                "execution_source": execution_source,
                "latency_ms": adapter_response.latency_ms,
                "status_code": adapter_response.status_code,
                "evaluation_id": evaluation.id,
                **(
                    {"target_execution_langfuse": target_langfuse_metadata}
                    if target_langfuse_metadata
                    else {}
                ),
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

    async def _build_attack_plan(
        self,
        *,
        campaign: Campaign,
        evaluation: Evaluation,
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], str | None, ExecutableAttackPayload | None]:
        deterministic_plan = {
            "source": "seeded_evaluation",
            "evaluation_id": evaluation.id,
            "evaluation_key": evaluation.key,
            "name": evaluation.name,
            "endpoint": evaluation.endpoint,
            "expected_behavior": evaluation.expected_behavior,
            "success_condition": evaluation.success_condition,
        }
        if campaign.llm_mode != "llm_assisted":
            return (
                deterministic_plan,
                {
                    "turns": [
                        {
                            "role": "red_team",
                            "content": "Deterministic seed attack selected from the regression suite.",
                        }
                    ]
                },
                {"agent": "red_team", "mode": "deterministic"},
                None,
                None,
            )

        settings = load_settings()
        if not settings.openrouter_api_key or not settings.red_team_model:
            return (
                {**deterministic_plan, "source": "llm_assisted_unconfigured"},
                {"turns": []},
                {"agent": "red_team", "mode": "llm_assisted", "prompt_version": RED_TEAM_ATTACK_PLAN_PROMPT_VERSION},
                "OpenRouter is not configured for llm_assisted campaigns",
                None,
            )

        requested_temperature = 0.7
        client = OpenRouterClient(
            api_key=settings.openrouter_api_key,
            base_url=settings.openrouter_base_url,
            site_url=settings.openrouter_site_url,
            app_title=settings.openrouter_app_title,
        )
        try:
            result = await client.chat_completion(
                model=settings.red_team_model,
                messages=red_team_attack_plan_messages(campaign=campaign, evaluation=evaluation),
                temperature=requested_temperature,
                max_completion_tokens=600,
                metadata={
                    "campaign_id": str(campaign.id),
                    "role": "red_team",
                    "prompt_version": RED_TEAM_ATTACK_PLAN_PROMPT_VERSION,
                },
                response_format={"type": "json_object"},
            )
        except Exception as exc:  # noqa: BLE001 - persisted as campaign evidence
            return (
                {**deterministic_plan, "source": "llm_assisted_error"},
                {"turns": []},
                {"agent": "red_team", "mode": "llm_assisted", "prompt_version": RED_TEAM_ATTACK_PLAN_PROMPT_VERSION},
                f"LLM attack-plan generation failed: {exc}",
                None,
            )

        plan_payload = self._parse_llm_json_content(result.content)
        executable_payload, normalized_payload, validation_warnings = self._normalize_llm_attack_plan(plan_payload)
        cost = result.cost_usd or 0.0
        campaign.spent_cost_usd += cost
        source = "openrouter_red_team" if executable_payload is not None else "openrouter_red_team_fallback"
        return (
            {
                **deterministic_plan,
                "source": source,
                "llm_plan": plan_payload,
                "executable_payload": normalized_payload,
                "validation_warnings": validation_warnings,
            },
            {
                "turns": [
                    {
                        "role": "red_team",
                        "content": result.content,
                    }
                ]
            },
            {
                "agent": "red_team",
                "mode": "llm_assisted",
                "provider": "openrouter",
                "requested_model": settings.red_team_model,
                "model": result.model,
                "prompt_version": RED_TEAM_ATTACK_PLAN_PROMPT_VERSION,
                "temperature": requested_temperature,
                "response_id": result.response_id,
                "usage": result.usage,
                "cost_usd": cost,
                **({"langfuse": result.langfuse_metadata} if result.langfuse_metadata else {}),
            },
            None,
            executable_payload,
        )

    @staticmethod
    def _parse_llm_json_content(content: str) -> dict[str, Any]:
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError:
            start = content.find("{")
            end = content.rfind("}")
            if start >= 0 and end > start:
                try:
                    parsed = json.loads(content[start : end + 1])
                except json.JSONDecodeError:
                    return {"raw_plan": content}
            else:
                return {"raw_plan": content}

        return parsed if isinstance(parsed, dict) else {"raw_plan": content}

    @staticmethod
    def _normalize_llm_attack_plan(
        plan_payload: dict[str, Any],
    ) -> tuple[ExecutableAttackPayload | None, dict[str, Any], list[str]]:
        warnings: list[str] = []
        endpoint = str(plan_payload.get("endpoint") or "/v1/chat")
        method = str(plan_payload.get("method") or "POST").upper()

        if endpoint != "/v1/chat":
            warnings.append(f"unsupported endpoint {endpoint}; falling back to seeded evaluation")
        if method != "POST":
            warnings.append(f"unsupported method {method}; falling back to seeded evaluation")

        messages = _normalize_messages(plan_payload)
        if not messages:
            warnings.append("missing executable message; falling back to seeded evaluation")

        document_context = plan_payload.get("document_context")
        if not isinstance(document_context, list):
            document_context = []

        expected_signal = plan_payload.get("expected_signal")
        normalized = {
            "endpoint": endpoint,
            "method": method,
            "messages": messages,
            "document_context": document_context,
            "expected_signal": expected_signal if isinstance(expected_signal, str) else None,
        }
        if warnings:
            return None, normalized, warnings

        return (
            ExecutableAttackPayload(
                endpoint=endpoint,
                method=method,
                messages=messages,
                document_context=document_context,
                expected_signal=normalized["expected_signal"],
            ),
            normalized,
            warnings,
        )

    async def _judge_attempt(self, state: CampaignGraphState) -> dict[str, Any]:
        attempt = self._load_attempt(state["last_attempt_id"])
        campaign = self._load_campaign(state["campaign_id"])
        evaluation_id = attempt.execution_metadata.get("evaluation_id")
        evaluation = self.db.get(Evaluation, evaluation_id) if isinstance(evaluation_id, int) else None

        if attempt.execution_metadata.get("execution_source") == "llm_attack_plan" and evaluation is not None:
            verdict = await self._judge_llm_attack_plan(campaign=campaign, attempt=attempt, evaluation=evaluation)
            attempt.status = "judged"
            campaign.last_activity_at = datetime.now(UTC)
            self.db.add(verdict)
            self.db.commit()
            self.db.refresh(verdict)
            return {"last_verdict": verdict.verdict}

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

    async def _judge_llm_attack_plan(
        self,
        *,
        campaign: Campaign,
        attempt: Attempt,
        evaluation: Evaluation,
    ) -> Verdict:
        settings = load_settings()
        judge_model = settings.judge_model or settings.red_team_model
        if not settings.openrouter_api_key or not judge_model:
            return Verdict(
                attempt_id=attempt.id,
                tier="llm",
                verdict="uncertain",
                severity="medium",
                confidence=0.2,
                rationale="OpenRouter judge is not configured",
                judge_model=judge_model,
                judge_prompt_version=LLM_JUDGE_ATTEMPT_PROMPT_VERSION,
                judge_temperature=0.0,
                raw_output={
                    "error": "OpenRouter judge is not configured",
                    "attempt_status": attempt.status,
                    "evaluation_id": evaluation.id,
                },
            )

        requested_temperature = 0.0
        client = OpenRouterClient(
            api_key=settings.openrouter_api_key,
            base_url=settings.openrouter_base_url,
            site_url=settings.openrouter_site_url,
            app_title=settings.openrouter_app_title,
        )
        try:
            result = await client.chat_completion(
                model=judge_model,
                messages=llm_judge_attempt_messages(
                    campaign=campaign,
                    evaluation=evaluation,
                    attack_plan=attempt.attack_plan,
                    request_json=attempt.request_json,
                    response_json=attempt.response_json,
                ),
                temperature=requested_temperature,
                max_completion_tokens=600,
                metadata={
                    "campaign_id": str(campaign.id),
                    "attempt_id": str(attempt.id),
                    "role": "judge",
                    "prompt_version": LLM_JUDGE_ATTEMPT_PROMPT_VERSION,
                },
                response_format={"type": "json_object"},
            )
        except Exception as exc:  # noqa: BLE001 - persisted as campaign evidence
            return Verdict(
                attempt_id=attempt.id,
                tier="llm",
                verdict="uncertain",
                severity="medium",
                confidence=0.2,
                rationale=f"LLM judge failed: {exc}",
                judge_model=judge_model,
                judge_prompt_version=LLM_JUDGE_ATTEMPT_PROMPT_VERSION,
                judge_temperature=requested_temperature,
                raw_output={
                    "error": str(exc),
                    "attempt_status": attempt.status,
                    "evaluation_id": evaluation.id,
                },
            )

        cost = result.cost_usd or 0.0
        campaign.spent_cost_usd += cost
        try:
            judge_payload = self._parse_llm_json_content(result.content)
            normalized = self._normalize_llm_judge_payload(judge_payload)
        except ValueError as exc:
            return Verdict(
                attempt_id=attempt.id,
                tier="llm",
                verdict="uncertain",
                severity="medium",
                confidence=0.2,
                rationale=f"LLM judge returned invalid JSON: {exc}",
                judge_model=result.model,
                judge_prompt_version=LLM_JUDGE_ATTEMPT_PROMPT_VERSION,
                judge_temperature=requested_temperature,
                raw_output={
                    "response_id": result.response_id,
                    "requested_model": judge_model,
                    "model": result.model,
                    "usage": result.usage,
                    "cost_usd": cost,
                    **({"langfuse": result.langfuse_metadata} if result.langfuse_metadata else {}),
                    "prompt_version": LLM_JUDGE_ATTEMPT_PROMPT_VERSION,
                    "error": str(exc),
                    "raw_content": result.content,
                    "attempt_status": attempt.status,
                    "evaluation_id": evaluation.id,
                },
            )
        return Verdict(
            attempt_id=attempt.id,
            tier="llm",
            verdict=normalized["verdict"],
            severity=normalized["severity"],
            confidence=normalized["confidence"],
            rationale=normalized["rationale"],
            judge_model=result.model,
            judge_prompt_version=LLM_JUDGE_ATTEMPT_PROMPT_VERSION,
            judge_temperature=requested_temperature,
            raw_output={
                "response_id": result.response_id,
                "requested_model": judge_model,
                "model": result.model,
                "usage": result.usage,
                "cost_usd": cost,
                **({"langfuse": result.langfuse_metadata} if result.langfuse_metadata else {}),
                "prompt_version": LLM_JUDGE_ATTEMPT_PROMPT_VERSION,
                "raw_judge": judge_payload,
                "normalized": normalized,
                "attempt_status": attempt.status,
                "evaluation_id": evaluation.id,
            },
        )

    @staticmethod
    def _normalize_llm_judge_payload(payload: dict[str, Any]) -> dict[str, Any]:
        verdict = str(payload.get("verdict", "uncertain")).lower()
        if verdict not in {"safe", "exploit", "uncertain"}:
            verdict = "uncertain"

        raw_severity = payload.get("severity")
        severity = str(raw_severity).lower() if isinstance(raw_severity, str) and raw_severity.strip() else None
        if verdict == "safe":
            severity = None
        elif severity not in {"low", "medium", "high", "critical"}:
            severity = "medium"

        raw_confidence = payload.get("confidence")
        if isinstance(raw_confidence, (int, float)):
            confidence = max(0.0, min(float(raw_confidence), 1.0))
        else:
            confidence = 0.5

        rationale = payload.get("rationale")
        if not isinstance(rationale, str) or not rationale.strip():
            rationale = "LLM judge did not provide a rationale"

        return {
            "verdict": verdict,
            "severity": severity,
            "confidence": confidence,
            "rationale": rationale.strip(),
            "observed_behavior": payload.get("observed_behavior"),
            "expected_behavior_match": payload.get("expected_behavior_match"),
        }

    async def _document_if_exploit(self, state: CampaignGraphState) -> dict[str, Any]:
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
        reproduction = build_reproduction(
            campaign=campaign,
            attempt=attempt,
            verdict=verdict,
            evaluation=evaluation,
        )
        finding = Finding(
            result_id=None,
            title=title,
            severity=severity,
            category_key=category_key,
            endpoint=endpoint,
            status="open",
            reproduction_steps=json.dumps(reproduction, indent=2, sort_keys=True, default=str),
            linked_attempt_id=attempt.id,
        )
        self.db.add(finding)
        self.db.flush()

        report_path = relative_report_path(finding.id)
        evaluation_json = draft_evaluation_json(
            evaluation=evaluation,
            finding=finding,
            attempt=attempt,
            verdict=verdict,
        )
        draft = PromotedEvalDraft(
            finding_id=finding.id,
            attempt_id=attempt.id,
            verdict_id=verdict.id,
            status="pending",
            evaluation_json=evaluation_json,
            report_path=report_path,
        )
        self.db.add(draft)
        self.db.flush()

        reproduction["draft"] = {
            "id": draft.id,
            "report_path": report_path,
            "evaluation_json": evaluation_json,
        }
        report_markdown, generation_metadata, documenter_cost = await render_report_markdown(
            campaign=campaign,
            finding=finding,
            attempt=attempt,
            verdict=verdict,
            evaluation=evaluation,
            reproduction=reproduction,
            draft_evaluation=evaluation_json,
        )
        written_report = write_report(
            finding_id=finding.id,
            markdown=report_markdown,
            generation_metadata=generation_metadata,
        )
        reproduction["report"] = {
            "path": written_report.report_path,
            "sha256": written_report.sha256,
            "size_bytes": written_report.size_bytes,
            "documenter": written_report.generation_metadata,
        }
        if documenter_cost:
            campaign.spent_cost_usd += documenter_cost
        finding.report_path = written_report.report_path
        finding.reproduction_steps = json.dumps(reproduction, indent=2, sort_keys=True, default=str)
        artifact = Artifact(
            owner_type="finding",
            owner_id=finding.id,
            kind="finding_report",
            storage_backend="filesystem",
            uri=written_report.report_path,
            sha256=written_report.sha256,
            mime_type="text/markdown",
            size_bytes=written_report.size_bytes,
            redaction_status="unreviewed",
        )
        attempt.status = "documented"
        campaign.exploit_count += 1
        campaign.last_activity_at = datetime.now(UTC)
        self.db.add(artifact)
        self.db.commit()
        return {}

    def _decide_continue(self, state: CampaignGraphState) -> dict[str, Any]:
        campaign = self._load_campaign(state["campaign_id"])
        attempts_run = state["attempts_run"] + 1
        campaign.attempt_count = attempts_run
        campaign.last_activity_at = datetime.now(UTC)

        budget_stop_reason = self._budget_stop_reason(campaign, campaign.last_activity_at)
        if budget_stop_reason:
            self._finish_budget_exhausted(campaign, now=campaign.last_activity_at, stop_reason=budget_stop_reason)
            campaign.summary = (
                f"{campaign.llm_mode} {campaign.target_mode_snapshot} campaign stopped after {attempts_run} attempts; "
                f"{campaign.exploit_count} exploit verdicts; {budget_stop_reason}."
            )
            self.db.commit()
            return {
                "attempts_run": attempts_run,
                "should_continue": False,
                "stop_reason": campaign.stop_reason,
            }

        if attempts_run >= campaign.max_attempts:
            campaign.status = "completed"
            campaign.stop_reason = "max_attempts_reached"
            campaign.finished_at = campaign.last_activity_at
            campaign.summary = (
                f"{campaign.llm_mode} {campaign.target_mode_snapshot} campaign completed {attempts_run} attempts; "
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

    def _budget_stop_reason(self, campaign: Campaign, now: datetime) -> str | None:
        if campaign.max_cost_usd <= 0:
            return "cost_budget_exhausted"
        if campaign.spent_cost_usd >= campaign.max_cost_usd * BUDGET_STOP_FRACTION:
            return "cost_budget_exhausted"

        started_at = _ensure_aware(campaign.started_at) if campaign.started_at is not None else now
        elapsed_seconds = max((now - started_at).total_seconds(), 0)
        if elapsed_seconds >= campaign.max_wall_clock_seconds * BUDGET_STOP_FRACTION:
            return "wall_clock_budget_exhausted"
        return None

    @staticmethod
    def _finish_budget_exhausted(campaign: Campaign, *, now: datetime, stop_reason: str) -> None:
        campaign.status = "budget_exhausted"
        campaign.stop_reason = stop_reason
        campaign.finished_at = now
        campaign.last_activity_at = now

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

    def _load_evaluation(self, evaluation_id: int | None) -> Evaluation:
        if evaluation_id is None:
            raise ValueError("selected evaluation id was not set")
        evaluation = self.db.get(Evaluation, evaluation_id)
        if evaluation is None:
            raise ValueError(f"evaluation {evaluation_id} was not found")
        return evaluation

    def _select_evaluation(self, campaign: Campaign, offset: int) -> EvaluationSelection:
        return OrchestratorRouter(self.db).select_evaluation(campaign, offset)


def _ensure_aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value
