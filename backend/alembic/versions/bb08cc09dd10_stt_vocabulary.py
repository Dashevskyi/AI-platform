"""tenant shell config: per-tenant STT vocabulary (initial_prompt + hotwords)

Revision ID: bb08cc09dd10
Revises: aa07bb08cc09
Create Date: 2026-06-02
"""
from alembic import op
import sqlalchemy as sa


revision = "bb08cc09dd10"
down_revision = "aa07bb08cc09"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tenant_shell_configs",
        sa.Column("stt_initial_prompt", sa.Text(), nullable=True),
    )
    op.add_column(
        "tenant_shell_configs",
        sa.Column("stt_hotwords", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("tenant_shell_configs", "stt_hotwords")
    op.drop_column("tenant_shell_configs", "stt_initial_prompt")
