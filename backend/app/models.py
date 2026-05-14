"""SQLAlchemy models for RedLens MVP state."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, Integer, JSON, String, Text, func, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Target(Base):
    __tablename__ = "targets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    mode: Mapped[str] = mapped_column(String(20), default="mock", index=True)
    base_url: Mapped[str] = mapped_column(String(500), default="mock://openemr")
    internal_auth_env: Mapped[str | None] = mapped_column(String(120), nullable=True)
    bearer_token_env: Mapped[str | None] = mapped_column(String(120), nullable=True)
    fhir_base_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    user_uuid: Mapped[str | None] = mapped_column(String(120), nullable=True)
    patient_uuid: Mapped[str | None] = mapped_column(String(120), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    runs: Mapped[list[EvaluationRun]] = relationship(back_populates="target")
    campaigns: Mapped[list[Campaign]] = relationship(back_populates="target")


class ThreatCategory(Base):
    __tablename__ = "threat_categories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(160))
    description: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    evaluations: Mapped[list[Evaluation]] = relationship(back_populates="category")


class Evaluation(Base):
    __tablename__ = "evaluations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(220))
    category_id: Mapped[int] = mapped_column(ForeignKey("threat_categories.id"))
    endpoint: Mapped[str] = mapped_column(String(120))
    method: Mapped[str] = mapped_column(String(10), default="POST")
    severity: Mapped[str] = mapped_column(String(20), default="medium")
    input_template: Mapped[dict[str, Any]] = mapped_column(JSON)
    expected_behavior: Mapped[str] = mapped_column(Text)
    success_condition: Mapped[str] = mapped_column(Text)
    judge: Mapped[dict[str, Any]] = mapped_column(JSON)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    category: Mapped[ThreatCategory] = relationship(back_populates="evaluations")
    results: Mapped[list[EvaluationResult]] = relationship(back_populates="evaluation")
    linked_findings: Mapped[list[Finding]] = relationship(
        back_populates="linked_evaluation",
        foreign_keys="Finding.linked_evaluation_id",
    )
    accepted_drafts: Mapped[list[PromotedEvalDraft]] = relationship(
        back_populates="accepted_evaluation",
    )


class EvaluationRun(Base):
    __tablename__ = "evaluation_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    target_id: Mapped[int] = mapped_column(ForeignKey("targets.id"))
    status: Mapped[str] = mapped_column(String(30), default="running", index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    total_count: Mapped[int] = mapped_column(Integer, default=0)
    passed_count: Mapped[int] = mapped_column(Integer, default=0)
    failed_count: Mapped[int] = mapped_column(Integer, default=0)
    error_count: Mapped[int] = mapped_column(Integer, default=0)
    campaign_id: Mapped[int | None] = mapped_column(ForeignKey("campaigns.id"), nullable=True, index=True)

    target: Mapped[Target] = relationship(back_populates="runs")
    results: Mapped[list[EvaluationResult]] = relationship(back_populates="run")
    campaign: Mapped[Campaign | None] = relationship(back_populates="evaluation_runs")


class EvaluationResult(Base):
    __tablename__ = "evaluation_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("evaluation_runs.id"), index=True)
    evaluation_id: Mapped[int] = mapped_column(ForeignKey("evaluations.id"), index=True)
    status: Mapped[str] = mapped_column(String(30), index=True)
    request_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    response_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    judge_name: Mapped[str] = mapped_column(String(120), default="deterministic")
    judge_reason: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    run: Mapped[EvaluationRun] = relationship(back_populates="results")
    evaluation: Mapped[Evaluation] = relationship(back_populates="results")
    finding: Mapped[Finding | None] = relationship(back_populates="result")


class Finding(Base):
    __tablename__ = "findings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    result_id: Mapped[int | None] = mapped_column(ForeignKey("evaluation_results.id"), unique=True, nullable=True)
    title: Mapped[str] = mapped_column(String(240))
    severity: Mapped[str] = mapped_column(String(20))
    category_key: Mapped[str] = mapped_column(String(80), index=True)
    endpoint: Mapped[str] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(30), default="open", index=True)
    reproduction_steps: Mapped[str] = mapped_column(Text)
    linked_attempt_id: Mapped[int | None] = mapped_column(ForeignKey("attempts.id"), nullable=True, index=True)
    linked_evaluation_id: Mapped[int | None] = mapped_column(ForeignKey("evaluations.id"), nullable=True, index=True)
    report_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    result: Mapped[EvaluationResult] = relationship(back_populates="finding")
    linked_attempt: Mapped[Attempt | None] = relationship(back_populates="findings")
    linked_evaluation: Mapped[Evaluation | None] = relationship(
        back_populates="linked_findings",
        foreign_keys=[linked_evaluation_id],
    )
    promoted_eval_drafts: Mapped[list[PromotedEvalDraft]] = relationship(back_populates="finding")


class Campaign(Base):
    __tablename__ = "campaigns"
    __table_args__ = (
        Index(
            "uq_campaigns_one_running_live_per_target",
            "target_id",
            unique=True,
            sqlite_where=text("target_mode_snapshot = 'live' AND status = 'running'"),
            postgresql_where=text("target_mode_snapshot = 'live' AND status = 'running'"),
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    target_id: Mapped[int] = mapped_column(ForeignKey("targets.id"), index=True)
    target_name_snapshot: Mapped[str] = mapped_column(String(120))
    target_mode_snapshot: Mapped[str] = mapped_column(String(20), index=True)
    target_base_url_snapshot: Mapped[str] = mapped_column(String(500))
    target_user_uuid_snapshot: Mapped[str | None] = mapped_column(String(120), nullable=True)
    target_patient_uuid_snapshot: Mapped[str | None] = mapped_column(String(120), nullable=True)
    status: Mapped[str] = mapped_column(String(40), default="draft", index=True)
    focus_hint: Mapped[str | None] = mapped_column(Text, nullable=True)
    llm_mode: Mapped[str] = mapped_column(String(30), default="deterministic")
    max_attempts: Mapped[int] = mapped_column(Integer, default=5)
    max_wall_clock_seconds: Mapped[int] = mapped_column(Integer, default=300)
    max_cost_usd: Mapped[float] = mapped_column(Float, default=0.5)
    spent_cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    exploit_count: Mapped[int] = mapped_column(Integer, default=0)
    stop_reason: Mapped[str | None] = mapped_column(String(120), nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    langfuse: Mapped[dict[str, Any] | None] = mapped_column(JSON, default=dict, nullable=True)
    live_approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_activity_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    target: Mapped[Target] = relationship(back_populates="campaigns")
    attempts: Mapped[list[Attempt]] = relationship(back_populates="campaign")
    evaluation_runs: Mapped[list[EvaluationRun]] = relationship(back_populates="campaign")


class Attempt(Base):
    __tablename__ = "attempts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    campaign_id: Mapped[int] = mapped_column(ForeignKey("campaigns.id"), index=True)
    target_id: Mapped[int] = mapped_column(ForeignKey("targets.id"), index=True)
    status: Mapped[str] = mapped_column(String(40), default="pending", index=True)
    focus_area: Mapped[str] = mapped_column(String(120), index=True)
    vector_key: Mapped[str | None] = mapped_column(String(160), nullable=True)
    attack_plan: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    transcript: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    request_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    response_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    execution_metadata: Mapped[dict[str, Any]] = mapped_column("metadata", JSON, default=dict)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    campaign: Mapped[Campaign] = relationship(back_populates="attempts")
    target: Mapped[Target] = relationship()
    verdicts: Mapped[list[Verdict]] = relationship(back_populates="attempt")
    findings: Mapped[list[Finding]] = relationship(back_populates="linked_attempt")
    promoted_eval_drafts: Mapped[list[PromotedEvalDraft]] = relationship(back_populates="attempt")


class Verdict(Base):
    __tablename__ = "verdicts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    attempt_id: Mapped[int] = mapped_column(ForeignKey("attempts.id"), index=True)
    tier: Mapped[str] = mapped_column(String(40), default="deterministic", index=True)
    verdict: Mapped[str] = mapped_column(String(40), index=True)
    severity: Mapped[str | None] = mapped_column(String(20), nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    rationale: Mapped[str] = mapped_column(Text)
    judge_model: Mapped[str | None] = mapped_column(String(160), nullable=True)
    judge_prompt_version: Mapped[str | None] = mapped_column(String(120), nullable=True)
    judge_temperature: Mapped[float | None] = mapped_column(Float, nullable=True)
    raw_output: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    attempt: Mapped[Attempt] = relationship(back_populates="verdicts")
    promoted_eval_drafts: Mapped[list[PromotedEvalDraft]] = relationship(back_populates="verdict")


class PromotedEvalDraft(Base):
    __tablename__ = "promoted_eval_drafts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    finding_id: Mapped[int] = mapped_column(ForeignKey("findings.id"), index=True)
    attempt_id: Mapped[int] = mapped_column(ForeignKey("attempts.id"), index=True)
    verdict_id: Mapped[int] = mapped_column(ForeignKey("verdicts.id"), index=True)
    status: Mapped[str] = mapped_column(String(40), default="pending", index=True)
    evaluation_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    report_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    review_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    accepted_evaluation_id: Mapped[int | None] = mapped_column(ForeignKey("evaluations.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    finding: Mapped[Finding] = relationship(back_populates="promoted_eval_drafts")
    attempt: Mapped[Attempt] = relationship(back_populates="promoted_eval_drafts")
    verdict: Mapped[Verdict] = relationship(back_populates="promoted_eval_drafts")
    accepted_evaluation: Mapped[Evaluation | None] = relationship(back_populates="accepted_drafts")


class Artifact(Base):
    __tablename__ = "artifacts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_type: Mapped[str] = mapped_column(String(40), index=True)
    owner_id: Mapped[int] = mapped_column(Integer, index=True)
    kind: Mapped[str] = mapped_column(String(80), index=True)
    storage_backend: Mapped[str] = mapped_column(String(40), default="db")
    uri: Mapped[str] = mapped_column(Text)
    sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    mime_type: Mapped[str | None] = mapped_column(String(120), nullable=True)
    size_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    redaction_status: Mapped[str] = mapped_column(String(40), default="unreviewed")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
