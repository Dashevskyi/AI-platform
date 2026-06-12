"""auto tool-limit settings on tenant_shell_configs

tool_limit_auto          — replace the flat max_tool_rounds cap with
                           intent-aware runaway guards
tool_limit_max_failures  — stop after N failed tool calls (auto mode)
tool_limit_max_per_tool  — stop if one tool is called > N times (auto mode)
tool_limit_plan_rounds   — round cap when a plan was registered (auto mode)

Revision ID: toollimitauto01
Revises: msgcontentemb01
Create Date: 2026-06-12
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "toollimitauto01"
down_revision: Union[str, None] = "msgcontentemb01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("tenant_shell_configs", sa.Column("tool_limit_auto", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("tenant_shell_configs", sa.Column("tool_limit_max_failures", sa.Integer(), nullable=False, server_default="4"))
    op.add_column("tenant_shell_configs", sa.Column("tool_limit_max_per_tool", sa.Integer(), nullable=False, server_default="4"))
    op.add_column("tenant_shell_configs", sa.Column("tool_limit_plan_rounds", sa.Integer(), nullable=False, server_default="20"))


def downgrade() -> None:
    op.drop_column("tenant_shell_configs", "tool_limit_plan_rounds")
    op.drop_column("tenant_shell_configs", "tool_limit_max_per_tool")
    op.drop_column("tenant_shell_configs", "tool_limit_max_failures")
    op.drop_column("tenant_shell_configs", "tool_limit_auto")
