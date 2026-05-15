"""add resume fields to messages + cross-chat recall flag

Revision ID: k1l2m3n4o5p6
Revises: j0k1l2m3n4o5
Create Date: 2026-05-15 00:00:00
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector

revision: str = "k1l2m3n4o5p6"
down_revision: Union[str, None] = "j0k1l2m3n4o5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("messages", sa.Column("resume_query", sa.Text(), nullable=True))
    op.add_column("messages", sa.Column("resume_response", sa.Text(), nullable=True))
    op.add_column("messages", sa.Column("resume_embedding", Vector(None), nullable=True))
    op.add_column("messages", sa.Column("resume_embedding_model", sa.String(200), nullable=True))
    op.add_column(
        "tenant_shell_configs",
        sa.Column(
            "recall_cross_chat_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )


def downgrade() -> None:
    op.drop_column("tenant_shell_configs", "recall_cross_chat_enabled")
    op.drop_column("messages", "resume_embedding_model")
    op.drop_column("messages", "resume_embedding")
    op.drop_column("messages", "resume_response")
    op.drop_column("messages", "resume_query")
