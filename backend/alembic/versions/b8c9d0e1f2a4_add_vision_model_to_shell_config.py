"""add vision_model_name to shell config

Revision ID: b8c9d0e1f2a4
Revises: d0e1f2a3b4c5
Create Date: 2026-05-04 04:30:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "b8c9d0e1f2a4"
down_revision = "d0e1f2a3b4c5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tenant_shell_configs",
        sa.Column("vision_model_name", sa.String(length=200), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("tenant_shell_configs", "vision_model_name")
