"""add exploration loop schema

Revision ID: 0002_exploration_loop
Revises: 0001_initial
Create Date: 2026-05-13
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0002_exploration_loop"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    _add_column_if_missing("targets", sa.Column("user_uuid", sa.String(length=120), nullable=True))

    if not _has_table("campaigns"):
        op.create_table(
            "campaigns",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("target_id", sa.Integer(), sa.ForeignKey("targets.id"), nullable=False),
            sa.Column("target_name_snapshot", sa.String(length=120), nullable=False),
            sa.Column("target_mode_snapshot", sa.String(length=20), nullable=False),
            sa.Column("target_base_url_snapshot", sa.String(length=500), nullable=False),
            sa.Column("target_user_uuid_snapshot", sa.String(length=120), nullable=True),
            sa.Column("target_patient_uuid_snapshot", sa.String(length=120), nullable=True),
            sa.Column("status", sa.String(length=40), nullable=False),
            sa.Column("focus_hint", sa.Text(), nullable=True),
            sa.Column("llm_mode", sa.String(length=30), nullable=False),
            sa.Column("max_attempts", sa.Integer(), nullable=False),
            sa.Column("max_wall_clock_seconds", sa.Integer(), nullable=False),
            sa.Column("max_cost_usd", sa.Float(), nullable=False),
            sa.Column("spent_cost_usd", sa.Float(), nullable=False),
            sa.Column("attempt_count", sa.Integer(), nullable=False),
            sa.Column("exploit_count", sa.Integer(), nullable=False),
            sa.Column("stop_reason", sa.String(length=120), nullable=True),
            sa.Column("summary", sa.Text(), nullable=True),
            sa.Column("live_approved_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("last_activity_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        )
    _create_index_if_missing("ix_campaigns_target_id", "campaigns", ["target_id"])
    _create_index_if_missing("ix_campaigns_target_mode_snapshot", "campaigns", ["target_mode_snapshot"])
    _create_index_if_missing("ix_campaigns_status", "campaigns", ["status"])
    _create_index_if_missing(
        "uq_campaigns_one_running_live_per_target",
        "campaigns",
        ["target_id"],
        unique=True,
        postgresql_where=sa.text("target_mode_snapshot = 'live' AND status = 'running'"),
        sqlite_where=sa.text("target_mode_snapshot = 'live' AND status = 'running'"),
    )

    _add_column_if_missing("evaluation_runs", sa.Column("campaign_id", sa.Integer(), nullable=True))
    _create_foreign_key_if_supported(
        "fk_evaluation_runs_campaign_id_campaigns",
        "evaluation_runs",
        "campaigns",
        ["campaign_id"],
        ["id"],
    )
    _create_index_if_missing("ix_evaluation_runs_campaign_id", "evaluation_runs", ["campaign_id"])

    if not _has_table("attempts"):
        op.create_table(
            "attempts",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("campaign_id", sa.Integer(), sa.ForeignKey("campaigns.id"), nullable=False),
            sa.Column("target_id", sa.Integer(), sa.ForeignKey("targets.id"), nullable=False),
            sa.Column("status", sa.String(length=40), nullable=False),
            sa.Column("focus_area", sa.String(length=120), nullable=False),
            sa.Column("vector_key", sa.String(length=160), nullable=True),
            sa.Column("attack_plan", sa.JSON(), nullable=False),
            sa.Column("transcript", sa.JSON(), nullable=False),
            sa.Column("request_json", sa.JSON(), nullable=False),
            sa.Column("response_json", sa.JSON(), nullable=False),
            sa.Column("metadata", sa.JSON(), nullable=False),
            sa.Column("error_message", sa.Text(), nullable=True),
            sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        )
    _create_index_if_missing("ix_attempts_campaign_id", "attempts", ["campaign_id"])
    _create_index_if_missing("ix_attempts_target_id", "attempts", ["target_id"])
    _create_index_if_missing("ix_attempts_status", "attempts", ["status"])
    _create_index_if_missing("ix_attempts_focus_area", "attempts", ["focus_area"])

    if not _has_table("verdicts"):
        op.create_table(
            "verdicts",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("attempt_id", sa.Integer(), sa.ForeignKey("attempts.id"), nullable=False),
            sa.Column("tier", sa.String(length=40), nullable=False),
            sa.Column("verdict", sa.String(length=40), nullable=False),
            sa.Column("severity", sa.String(length=20), nullable=True),
            sa.Column("confidence", sa.Float(), nullable=True),
            sa.Column("rationale", sa.Text(), nullable=False),
            sa.Column("judge_model", sa.String(length=160), nullable=True),
            sa.Column("judge_prompt_version", sa.String(length=120), nullable=True),
            sa.Column("judge_temperature", sa.Float(), nullable=True),
            sa.Column("raw_output", sa.JSON(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        )
    _create_index_if_missing("ix_verdicts_attempt_id", "verdicts", ["attempt_id"])
    _create_index_if_missing("ix_verdicts_tier", "verdicts", ["tier"])
    _create_index_if_missing("ix_verdicts_verdict", "verdicts", ["verdict"])

    _add_column_if_missing("findings", sa.Column("linked_attempt_id", sa.Integer(), nullable=True))
    _add_column_if_missing("findings", sa.Column("linked_evaluation_id", sa.Integer(), nullable=True))
    _add_column_if_missing("findings", sa.Column("report_path", sa.String(length=500), nullable=True))
    _create_foreign_key_if_supported(
        "fk_findings_linked_attempt_id_attempts",
        "findings",
        "attempts",
        ["linked_attempt_id"],
        ["id"],
    )
    _create_foreign_key_if_supported(
        "fk_findings_linked_evaluation_id_evaluations",
        "findings",
        "evaluations",
        ["linked_evaluation_id"],
        ["id"],
    )
    _create_index_if_missing("ix_findings_linked_attempt_id", "findings", ["linked_attempt_id"])
    _create_index_if_missing("ix_findings_linked_evaluation_id", "findings", ["linked_evaluation_id"])

    if not _has_table("promoted_eval_drafts"):
        op.create_table(
            "promoted_eval_drafts",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("finding_id", sa.Integer(), sa.ForeignKey("findings.id"), nullable=False),
            sa.Column("attempt_id", sa.Integer(), sa.ForeignKey("attempts.id"), nullable=False),
            sa.Column("verdict_id", sa.Integer(), sa.ForeignKey("verdicts.id"), nullable=False),
            sa.Column("status", sa.String(length=40), nullable=False),
            sa.Column("evaluation_json", sa.JSON(), nullable=False),
            sa.Column("report_path", sa.String(length=500), nullable=True),
            sa.Column("review_notes", sa.Text(), nullable=True),
            sa.Column("accepted_evaluation_id", sa.Integer(), sa.ForeignKey("evaluations.id"), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        )
    _create_index_if_missing("ix_promoted_eval_drafts_finding_id", "promoted_eval_drafts", ["finding_id"])
    _create_index_if_missing("ix_promoted_eval_drafts_attempt_id", "promoted_eval_drafts", ["attempt_id"])
    _create_index_if_missing("ix_promoted_eval_drafts_verdict_id", "promoted_eval_drafts", ["verdict_id"])
    _create_index_if_missing("ix_promoted_eval_drafts_status", "promoted_eval_drafts", ["status"])

    if not _has_table("artifacts"):
        op.create_table(
            "artifacts",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("owner_type", sa.String(length=40), nullable=False),
            sa.Column("owner_id", sa.Integer(), nullable=False),
            sa.Column("kind", sa.String(length=80), nullable=False),
            sa.Column("storage_backend", sa.String(length=40), nullable=False),
            sa.Column("uri", sa.Text(), nullable=False),
            sa.Column("sha256", sa.String(length=64), nullable=True),
            sa.Column("mime_type", sa.String(length=120), nullable=True),
            sa.Column("size_bytes", sa.Integer(), nullable=True),
            sa.Column("redaction_status", sa.String(length=40), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        )
    _create_index_if_missing("ix_artifacts_owner_type", "artifacts", ["owner_type"])
    _create_index_if_missing("ix_artifacts_owner_id", "artifacts", ["owner_id"])
    _create_index_if_missing("ix_artifacts_kind", "artifacts", ["kind"])


def downgrade() -> None:
    op.drop_table("artifacts")
    op.drop_table("promoted_eval_drafts")
    op.drop_index("ix_findings_linked_evaluation_id", table_name="findings")
    op.drop_index("ix_findings_linked_attempt_id", table_name="findings")
    op.drop_constraint("fk_findings_linked_evaluation_id_evaluations", "findings", type_="foreignkey")
    op.drop_constraint("fk_findings_linked_attempt_id_attempts", "findings", type_="foreignkey")
    op.drop_column("findings", "report_path")
    op.drop_column("findings", "linked_evaluation_id")
    op.drop_column("findings", "linked_attempt_id")
    op.drop_table("verdicts")
    op.drop_table("attempts")
    op.drop_index("ix_evaluation_runs_campaign_id", table_name="evaluation_runs")
    op.drop_constraint("fk_evaluation_runs_campaign_id_campaigns", "evaluation_runs", type_="foreignkey")
    op.drop_column("evaluation_runs", "campaign_id")
    op.drop_table("campaigns")
    op.drop_column("targets", "user_uuid")


def _inspector():
    return sa.inspect(op.get_bind())


def _has_table(table_name: str) -> bool:
    return table_name in _inspector().get_table_names()


def _has_column(table_name: str, column_name: str) -> bool:
    if not _has_table(table_name):
        return False
    return any(column["name"] == column_name for column in _inspector().get_columns(table_name))


def _has_index(table_name: str, index_name: str) -> bool:
    if not _has_table(table_name):
        return False
    return any(index["name"] == index_name for index in _inspector().get_indexes(table_name))


def _has_foreign_key(table_name: str, constraint_name: str) -> bool:
    if not _has_table(table_name):
        return False
    return any(fk.get("name") == constraint_name for fk in _inspector().get_foreign_keys(table_name))


def _add_column_if_missing(table_name: str, column: sa.Column) -> None:
    if not _has_column(table_name, column.name):
        op.add_column(table_name, column)


def _create_index_if_missing(index_name: str, table_name: str, columns: list[str], **kwargs: object) -> None:
    if not _has_index(table_name, index_name):
        op.create_index(index_name, table_name, columns, **kwargs)


def _create_foreign_key_if_supported(
    constraint_name: str,
    source_table: str,
    referent_table: str,
    local_cols: list[str],
    remote_cols: list[str],
) -> None:
    if op.get_bind().dialect.name == "sqlite":
        return
    if not _has_foreign_key(source_table, constraint_name):
        op.create_foreign_key(constraint_name, source_table, referent_table, local_cols, remote_cols)
