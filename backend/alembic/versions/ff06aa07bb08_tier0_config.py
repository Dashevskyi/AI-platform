"""tenant shell config: tier 0 routing fields

Revision ID: ff06aa07bb08
Revises: ee05ff06aa07
Create Date: 2026-05-24
"""
from alembic import op
import sqlalchemy as sa


revision = "ff06aa07bb08"
down_revision = "ee05ff06aa07"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tenant_shell_configs",
        sa.Column("tier0_enabled", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.add_column(
        "tenant_shell_configs",
        sa.Column("tier0_min_tool_score", sa.Float(), nullable=False, server_default="0.80"),
    )
    op.add_column(
        "tenant_shell_configs",
        sa.Column("tier0_max_score_gap", sa.Float(), nullable=False, server_default="0.15"),
    )


def downgrade() -> None:
    op.drop_column("tenant_shell_configs", "tier0_max_score_gap")
    op.drop_column("tenant_shell_configs", "tier0_min_tool_score")
    op.drop_column("tenant_shell_configs", "tier0_enabled")
