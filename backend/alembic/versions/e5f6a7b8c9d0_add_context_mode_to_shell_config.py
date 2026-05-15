"""add context_mode to shell config

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-04-29 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "e5f6a7b8c9d0"
down_revision = "d4e5f6a7b8c9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tenant_shell_configs",
        sa.Column("context_mode", sa.String(length=50), nullable=False, server_default="summary_plus_recent"),
    )
    op.execute(
        "UPDATE tenant_shell_configs SET context_mode = 'summary_plus_recent' WHERE context_mode IS NULL"
    )
    op.alter_column("tenant_shell_configs", "context_mode", server_default=None)


def downgrade() -> None:
    op.drop_column("tenant_shell_configs", "context_mode")
