"""SQLAlchemy models for RedLens MVP state."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, JSON, String, Text, func
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
    patient_uuid: Mapped[str | None] = mapped_column(String(120), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    runs: Mapped[list[EvaluationRun]] = relationship(back_populates="target")


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

    target: Mapped[Target] = relationship(back_populates="runs")
    results: Mapped[list[EvaluationResult]] = relationship(back_populates="run")


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
    result_id: Mapped[int] = mapped_column(ForeignKey("evaluation_results.id"), unique=True)
    title: Mapped[str] = mapped_column(String(240))
    severity: Mapped[str] = mapped_column(String(20))
    category_key: Mapped[str] = mapped_column(String(80), index=True)
    endpoint: Mapped[str] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(30), default="open", index=True)
    reproduction_steps: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    result: Mapped[EvaluationResult] = relationship(back_populates="finding")

