"""API schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


TargetMode = Literal["mock", "live"]


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
    created_at: datetime


class RunDetail(RunSummary):
    results: list[ResultRead]


class FindingRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    result_id: int
    title: str
    severity: str
    category_key: str
    endpoint: str
    status: str
    reproduction_steps: str
    created_at: datetime


class ErrorMessage(BaseModel):
    detail: str

