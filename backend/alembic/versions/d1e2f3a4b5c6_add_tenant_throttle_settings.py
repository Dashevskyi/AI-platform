"""add tenant throttle settings

Revision ID: d1e2f3a4b5c6
Revises: c0d1e2f3a4b5
Create Date: 2026-05-08 18:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "d1e2f3a4b5c6"
down_revision = "c0d1e2f3a4b5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("tenants", sa.Column("throttle_enabled", sa.Boolean(), nullable=False, server_default=sa.text("false")))
    op.add_column("tenants", sa.Column("throttle_max_concurrent", sa.Integer(), nullable=False, server_default="5"))
    op.add_column(
        "tenants",
        sa.Column("throttle_overflow_policy", sa.String(20), nullable=False, server_default="reject_429"),
    )
    op.add_column("tenants", sa.Column("throttle_queue_max", sa.Integer(), nullable=False, server_default="20"))


def downgrade() -> None:
    op.drop_column("tenants", "throttle_queue_max")
    op.drop_column("tenants", "throttle_overflow_policy")
    op.drop_column("tenants", "throttle_max_concurrent")
    op.drop_column("tenants", "throttle_enabled")
