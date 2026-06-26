"""assistant tune recommendations (auto-tuning staging table)

Read-only audit→diagnose loop stages proposed config changes here; nothing
touches live tool/assistant config until an admin applies a specific row.

Revision ID: tune01
Revises: auditsuite01
Create Date: 2026-06-25 00:00:00
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "tune01"
down_revision: Union[str, None] = "auditsuite01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "assistant_tune_recommendations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False, index=True),
        sa.Column("assistant_id", postgresql.UUID(as_uuid=True), nullable=False, index=True),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("scope", sa.Text(), nullable=False, server_default="tool"),
        sa.Column("tool_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("tool_name", sa.Text(), nullable=True),
        sa.Column("change_type", sa.Text(), nullable=False),
        sa.Column("json_path", sa.Text(), nullable=True),
        sa.Column("param_name", sa.Text(), nullable=True),
        sa.Column("current_value", postgresql.JSONB(), nullable=True),
        sa.Column("proposed_value", postgresql.JSONB(), nullable=True),
        sa.Column("rationale", sa.Text(), nullable=True),
        sa.Column("deterministic", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("failing_case_ids", postgresql.JSONB(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False, server_default="pending"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_tune_rec_assistant_status",
        "assistant_tune_recommendations", ["assistant_id", "status"],
    )


def downgrade() -> None:
    op.drop_index("ix_tune_rec_assistant_status", table_name="assistant_tune_recommendations")
    op.drop_table("assistant_tune_recommendations")
