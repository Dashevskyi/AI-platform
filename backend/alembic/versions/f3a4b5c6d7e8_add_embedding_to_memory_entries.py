"""add embedding column to memory_entries

Revision ID: f3a4b5c6d7e8
Revises: e2f3a4b5c6d7
Create Date: 2026-05-09 22:00:00.000000
"""

from alembic import op
import sqlalchemy as sa
from pgvector.sqlalchemy import Vector


revision = "f3a4b5c6d7e8"
down_revision = "e2f3a4b5c6d7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "memory_entries",
        sa.Column("embedding", Vector(None), nullable=True),
    )
    op.add_column(
        "memory_entries",
        sa.Column("embedding_model", sa.String(200), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("memory_entries", "embedding_model")
    op.drop_column("memory_entries", "embedding")
