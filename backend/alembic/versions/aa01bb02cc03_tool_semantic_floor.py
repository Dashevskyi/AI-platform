"""add tool_semantic_floor to tenant_shell_configs

Per-tenant similarity floor for semantic tool selection. Tools whose cosine
similarity to the user query falls below this threshold are excluded from
the prompt. Previously there was no floor — top-K was always sent regardless
of quality, leading to confusing the model with low-relevance tools.

Default 0.5: empirically a balanced cut for bge-m3 with well-formed
descriptions. Admins can lower it (0.3) when descriptions are noisy or
raise (0.65) when only high-quality matches are wanted.

Revision ID: aa01bb02cc03
Revises: z6a7b8c9d0e1
Create Date: 2026-05-16 06:00:00
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "aa01bb02cc03"
down_revision: Union[str, None] = "z6a7b8c9d0e1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "tenant_shell_configs",
        sa.Column("tool_semantic_floor", sa.Float(), nullable=False, server_default="0.5"),
    )


def downgrade() -> None:
    op.drop_column("tenant_shell_configs", "tool_semantic_floor")
