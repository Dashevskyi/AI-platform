"""add gpu_metric_snapshots

Revision ID: g7h8i9j0k1l2
Revises: f6a7b8c9d0e1
Create Date: 2026-05-14 00:30:00
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "g7h8i9j0k1l2"
down_revision: Union[str, None] = "a4b5c6d7e8f9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "gpu_metric_snapshots",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("gpus", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("vllm", postgresql.JSONB(), nullable=True),
    )
    op.create_index(
        "ix_gpu_metric_snapshots_created_at",
        "gpu_metric_snapshots",
        ["created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_gpu_metric_snapshots_created_at", table_name="gpu_metric_snapshots")
    op.drop_table("gpu_metric_snapshots")
