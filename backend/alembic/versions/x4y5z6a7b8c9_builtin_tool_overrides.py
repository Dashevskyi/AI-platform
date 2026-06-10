"""add builtin_tool_overrides table

Builtin tools (memory/artifacts/RAG/time) live in code so handlers and the
parameter schema stay locked. But the description — what the model reads to
decide WHEN to call the tool — needs to be tweakable from the UI without a
deploy. Per-tenant override row: presence = override, absence = default.

Revision ID: x4y5z6a7b8c9
Revises: w3x4y5z6a7b8
Create Date: 2026-05-16 02:00:00
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "x4y5z6a7b8c9"
down_revision: Union[str, None] = "w3x4y5z6a7b8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "builtin_tool_overrides",
        sa.Column("tenant_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tool_name", sa.String(100), nullable=False),
        sa.Column("description", sa.Text, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("tenant_id", "tool_name"),
    )


def downgrade() -> None:
    op.drop_table("builtin_tool_overrides")
