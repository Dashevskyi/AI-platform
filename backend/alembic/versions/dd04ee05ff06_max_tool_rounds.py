"""add max_tool_rounds to tenant_shell_configs

Per-tenant override of the tool-routing loop cap. Default 6 — was hardcoded
as MAX_TOOL_ROUNDS in pipeline.py. Tenants doing multi-stage data pipelines
that genuinely need >6 steps can raise it; rest can lower to fail fast.

Revision ID: dd04ee05ff06
Revises: cc03dd04ee05
Create Date: 2026-05-17 00:30:00
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "dd04ee05ff06"
down_revision: Union[str, None] = "cc03dd04ee05"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "tenant_shell_configs",
        sa.Column("max_tool_rounds", sa.Integer(), nullable=False, server_default="6"),
    )


def downgrade() -> None:
    op.drop_column("tenant_shell_configs", "max_tool_rounds")
