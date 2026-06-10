"""add tool_routing_temperature to tenant_shell_configs

Per-tenant temperature override applied ONLY to rounds where the LLM has
access to tools (i.e. it might call one). Lower temp = more deterministic
tool selection. Ordinary chat rounds (no tools available) keep the default
config.temperature, since creativity is still wanted there.

Default 0.3: empirically a balanced cut for Qwen/DeepSeek tool routing
without making the model too rigid.

Revision ID: bb02cc03dd04
Revises: aa01bb02cc03
Create Date: 2026-05-16 17:00:00
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "bb02cc03dd04"
down_revision: Union[str, None] = "aa01bb02cc03"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "tenant_shell_configs",
        sa.Column("tool_routing_temperature", sa.Float(), nullable=False, server_default="0.3"),
    )


def downgrade() -> None:
    op.drop_column("tenant_shell_configs", "tool_routing_temperature")
