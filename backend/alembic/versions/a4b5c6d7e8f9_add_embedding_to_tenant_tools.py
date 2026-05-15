"""add embedding + is_pinned to tenant_tools

Revision ID: a4b5c6d7e8f9
Revises: f3a4b5c6d7e8
Create Date: 2026-05-09 23:00:00.000000
"""

from alembic import op
import sqlalchemy as sa
from pgvector.sqlalchemy import Vector


revision = "a4b5c6d7e8f9"
down_revision = "f3a4b5c6d7e8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("tenant_tools", sa.Column("embedding", Vector(None), nullable=True))
    op.add_column("tenant_tools", sa.Column("embedding_model", sa.String(200), nullable=True))
    op.add_column(
        "tenant_tools",
        sa.Column("is_pinned", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )


def downgrade() -> None:
    op.drop_column("tenant_tools", "is_pinned")
    op.drop_column("tenant_tools", "embedding_model")
    op.drop_column("tenant_tools", "embedding")
