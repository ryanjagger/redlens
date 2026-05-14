"""allow exploration findings without evaluation results

Revision ID: 0003_nullable_exploration_findings
Revises: 0002_exploration_loop
Create Date: 2026-05-13
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0003_nullable_findings"
down_revision = "0002_exploration_loop"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "findings",
        "result_id",
        existing_type=sa.Integer(),
        nullable=True,
    )


def downgrade() -> None:
    op.alter_column(
        "findings",
        "result_id",
        existing_type=sa.Integer(),
        nullable=False,
    )
