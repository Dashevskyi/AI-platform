"""add debug_enabled flag to tenant_shell_configs

Per-tenant switch for accumulating the debug JSON on each LLM call. Default
True (matches current behaviour). When False, pipeline skips writing the
debug trace — useful for "we're done analysing this tenant, stop bloating
the logs table" mode.

Revision ID: y5z6a7b8c9d0
Revises: x4y5z6a7b8c9
Create Date: 2026-05-16 05:00:00
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "y5z6a7b8c9d0"
down_revision: Union[str, None] = "x4y5z6a7b8c9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "tenant_shell_configs",
        sa.Column("debug_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
    )


def downgrade() -> None:
    op.drop_column("tenant_shell_configs", "debug_enabled")
