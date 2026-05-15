"""add tenant message-merge settings

Revision ID: e2f3a4b5c6d7
Revises: d1e2f3a4b5c6
Create Date: 2026-05-08 19:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "e2f3a4b5c6d7"
down_revision = "d1e2f3a4b5c6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tenants",
        sa.Column("merge_messages_enabled", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.add_column(
        "tenants",
        sa.Column("merge_window_ms", sa.Integer(), nullable=False, server_default="1500"),
    )


def downgrade() -> None:
    op.drop_column("tenants", "merge_window_ms")
    op.drop_column("tenants", "merge_messages_enabled")
