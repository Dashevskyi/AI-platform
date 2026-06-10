"""tenant shell config: PII routing safeguard

Revision ID: aa07bb08cc09
Revises: ff06aa07bb08
Create Date: 2026-05-24
"""
from alembic import op
import sqlalchemy as sa


revision = "aa07bb08cc09"
down_revision = "ff06aa07bb08"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tenant_shell_configs",
        sa.Column("pii_routing_enabled", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )


def downgrade() -> None:
    op.drop_column("tenant_shell_configs", "pii_routing_enabled")
