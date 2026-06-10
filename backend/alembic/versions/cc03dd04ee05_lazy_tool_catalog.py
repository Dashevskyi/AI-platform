"""add lazy_tool_catalog_topk to tenant_shell_configs

Lazy tool catalog: send full schema only for top-K tools by semantic score;
list the rest in a compact system block (name + 1-line + tags). Model can
inspect them via builtin `describe_tool(name)` or just call by name —
pipeline adds the full schema to the payload on the next round.

Default 3: a balance between fast direct-call path for the most-likely
tools and token savings for the rest. Set to a large value (e.g. 100) to
effectively disable the feature.

Revision ID: cc03dd04ee05
Revises: bb02cc03dd04
Create Date: 2026-05-16 18:00:00
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "cc03dd04ee05"
down_revision: Union[str, None] = "bb02cc03dd04"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "tenant_shell_configs",
        sa.Column("lazy_tool_catalog_topk", sa.Integer(), nullable=False, server_default="3"),
    )


def downgrade() -> None:
    op.drop_column("tenant_shell_configs", "lazy_tool_catalog_topk")
