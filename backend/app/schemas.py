"""API schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


TargetMode = Literal["mock", "live"]
CampaignStatus = Literal[
    "draft",
    "needs_live_approval",
    "running",
    "completed",
    "budget_exhausted",
    "cancelled",
    "failed",
]
LlmMode = Literal["deterministic", "llm_assisted"]


class TargetCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    mode: TargetMode = "mock"
    base_url: str = "mock://openemr"
    internal_auth_env: str | None = None
    bearer_token_env: str | None = None
    fhir_base_url: str | None = None
    user_uuid: str | None = None
    patient_uuid: str | None = None


class TargetRead(TargetCreate):
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime


class ThreatCategoryRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    key: str
    name: str
    description: str
    evaluation_count: int = 0
    last_failed_count: int = 0


class EvaluationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    key: str
    name: str
    category_id: int
    category_key: str
    category_name: str
    endpoint: str
    method: str
    severity: str
    expected_behavior: str
    success_condition: str
    enabled: bool
    last_status: str | None = None


class RunCreate(BaseModel):
    target_id: int
    evaluation_ids: list[int] | None = None


class CampaignCreate(BaseModel):
    target_id: int
    focus_hint: str | None = None
    max_attempts: int = Field(default=5, ge=1, le=500)
    max_wall_clock_seconds: int = Field(default=300, ge=1, le=86400)
    max_cost_usd: float = Field(default=0.5, ge=0)
    llm_mode: LlmMode = "deterministic"


class CampaignRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    target_id: int
    target_name_snapshot: str
    target_mode_snapshot: str
    target_base_url_snapshot: str
    target_user_uuid_snapshot: str | None
    target_patient_uuid_snapshot: str | None
    status: str
    focus_hint: str | None
    llm_mode: str
    max_attempts: int
    max_wall_clock_seconds: int
    max_cost_usd: float
    spent_cost_usd: float
    attempt_count: int
    exploit_count: int
    stop_reason: str | None
    summary: str | None
    live_approved_at: datetime | None
    started_at: datetime | None
    finished_at: datetime | None
    last_activity_at: datetime
    created_at: datetime


class VerdictRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    attempt_id: int
    tier: str
    verdict: str
    severity: str | None
    confidence: float | None
    rationale: str
    judge_model: str | None
    judge_prompt_version: str | None
    judge_temperature: float | None
    raw_output: dict[str, Any]
    created_at: datetime


class PromotedEvalDraftRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    finding_id: int
    attempt_id: int
    verdict_id: int
    status: str
    evaluation_json: dict[str, Any]
    report_path: str | None
    review_notes: str | None
    accepted_evaluation_id: int | None
    created_at: datetime
    reviewed_at: datetime | None


class DraftReviewUpdate(BaseModel):
    review_notes: str | None = None


class AttemptRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    campaign_id: int
    target_id: int
    status: str
    focus_area: str
    vector_key: str | None
    attack_plan: dict[str, Any]
    transcript: dict[str, Any]
    request_json: dict[str, Any]
    response_json: dict[str, Any]
    execution_metadata: dict[str, Any]
    error_message: str | None
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime
    verdicts: list[VerdictRead] = []
    promoted_eval_drafts: list[PromotedEvalDraftRead] = []


class CampaignDetail(CampaignRead):
    attempts: list[AttemptRead]


class RunSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    target_id: int
    target_name: str
    status: str
    started_at: datetime
    finished_at: datetime | None
    total_count: int
    passed_count: int
    failed_count: int
    error_count: int


class ResultRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    run_id: int
    evaluation_id: int
    evaluation_key: str
    evaluation_name: str
    category_key: str
    category_name: str
    endpoint: str
    severity: str
    status: str
    request_json: dict[str, Any]
    response_json: dict[str, Any]
    status_code: int | None
    latency_ms: int
    judge_name: str
    judge_reason: str
    origin_finding_id: int | None = None
    origin_finding_status: str | None = None
    origin_report_path: str | None = None
    origin_draft_id: int | None = None
    created_at: datetime


class RunDetail(RunSummary):
    results: list[ResultRead]


class FindingRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    result_id: int | None
    title: str
    severity: str
    category_key: str
    endpoint: str
    status: str
    reproduction_steps: str
    linked_attempt_id: int | None = None
    linked_evaluation_id: int | None = None
    report_path: str | None = None
    created_at: datetime


class FindingReportRead(BaseModel):
    finding_id: int
    report_path: str
    content: str
    artifact_id: int | None = None
    storage_backend: str
    sha256: str
    mime_type: str
    size_bytes: int
    redaction_status: str
    generation_metadata: dict[str, Any] = Field(default_factory=dict)


class ErrorMessage(BaseModel):
    detail: str
