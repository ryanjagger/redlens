"""persist campaign langfuse trace metadata

Revision ID: 0004_campaign_langfuse
Revises: 0003_nullable_findings
Create Date: 2026-05-13
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0004_campaign_langfuse"
down_revision = "0003_nullable_findings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("campaigns", sa.Column("langfuse", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("campaigns", "langfuse")
