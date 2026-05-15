"""add embedding_model column to kb_chunks and message_attachment_chunks

These tables track embeddings but historically didn't store which model
produced them, so a model swap had no clean way to filter out stale rows
during backfill. Add the column to bring them in line with artifacts /
memory_entries / messages / tenant_tools.

Revision ID: t0u1v2w3x4y5
Revises: s9t0u1v2w3x4
Create Date: 2026-05-16 00:00:00
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "t0u1v2w3x4y5"
down_revision: Union[str, None] = "s9t0u1v2w3x4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("kb_chunks", sa.Column("embedding_model", sa.String(200), nullable=True))
    op.add_column("message_attachment_chunks", sa.Column("embedding_model", sa.String(200), nullable=True))


def downgrade() -> None:
    op.drop_column("message_attachment_chunks", "embedding_model")
    op.drop_column("kb_chunks", "embedding_model")
