"""initial RedLens MVP schema

Revision ID: 0001_initial
Revises:
Create Date: 2026-05-12
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "targets",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("mode", sa.String(length=20), nullable=False),
        sa.Column("base_url", sa.String(length=500), nullable=False),
        sa.Column("internal_auth_env", sa.String(length=120), nullable=True),
        sa.Column("bearer_token_env", sa.String(length=120), nullable=True),
        sa.Column("fhir_base_url", sa.String(length=500), nullable=True),
        sa.Column("patient_uuid", sa.String(length=120), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_targets_name", "targets", ["name"], unique=True)
    op.create_index("ix_targets_mode", "targets", ["mode"])

    op.create_table(
        "threat_categories",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("key", sa.String(length=80), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_threat_categories_key", "threat_categories", ["key"], unique=True)

    op.create_table(
        "evaluations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("key", sa.String(length=120), nullable=False),
        sa.Column("name", sa.String(length=220), nullable=False),
        sa.Column("category_id", sa.Integer(), sa.ForeignKey("threat_categories.id"), nullable=False),
        sa.Column("endpoint", sa.String(length=120), nullable=False),
        sa.Column("method", sa.String(length=10), nullable=False),
        sa.Column("severity", sa.String(length=20), nullable=False),
        sa.Column("input_template", sa.JSON(), nullable=False),
        sa.Column("expected_behavior", sa.Text(), nullable=False),
        sa.Column("success_condition", sa.Text(), nullable=False),
        sa.Column("judge", sa.JSON(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_evaluations_key", "evaluations", ["key"], unique=True)
    op.create_index("ix_evaluations_enabled", "evaluations", ["enabled"])

    op.create_table(
        "evaluation_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("target_id", sa.Integer(), sa.ForeignKey("targets.id"), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("total_count", sa.Integer(), nullable=False),
        sa.Column("passed_count", sa.Integer(), nullable=False),
        sa.Column("failed_count", sa.Integer(), nullable=False),
        sa.Column("error_count", sa.Integer(), nullable=False),
    )
    op.create_index("ix_evaluation_runs_status", "evaluation_runs", ["status"])

    op.create_table(
        "evaluation_results",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("run_id", sa.Integer(), sa.ForeignKey("evaluation_runs.id"), nullable=False),
        sa.Column("evaluation_id", sa.Integer(), sa.ForeignKey("evaluations.id"), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("request_json", sa.JSON(), nullable=False),
        sa.Column("response_json", sa.JSON(), nullable=False),
        sa.Column("status_code", sa.Integer(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("judge_name", sa.String(length=120), nullable=False),
        sa.Column("judge_reason", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_evaluation_results_run_id", "evaluation_results", ["run_id"])
    op.create_index("ix_evaluation_results_evaluation_id", "evaluation_results", ["evaluation_id"])
    op.create_index("ix_evaluation_results_status", "evaluation_results", ["status"])

    op.create_table(
        "findings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("result_id", sa.Integer(), sa.ForeignKey("evaluation_results.id"), nullable=False),
        sa.Column("title", sa.String(length=240), nullable=False),
        sa.Column("severity", sa.String(length=20), nullable=False),
        sa.Column("category_key", sa.String(length=80), nullable=False),
        sa.Column("endpoint", sa.String(length=120), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("reproduction_steps", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_findings_result_id", "findings", ["result_id"], unique=True)
    op.create_index("ix_findings_category_key", "findings", ["category_key"])
    op.create_index("ix_findings_status", "findings", ["status"])


def downgrade() -> None:
    op.drop_table("findings")
    op.drop_table("evaluation_results")
    op.drop_table("evaluation_runs")
    op.drop_table("evaluations")
    op.drop_table("threat_categories")
    op.drop_table("targets")

