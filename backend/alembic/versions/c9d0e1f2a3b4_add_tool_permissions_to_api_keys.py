"""add tool permissions to api keys and groups

Revision ID: c9d0e1f2a3b4
Revises: b8c9d0e1f2a3
Create Date: 2026-05-02 12:30:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = "c9d0e1f2a3b4"
down_revision = "b8c9d0e1f2a3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tenant_api_key_groups",
        sa.Column("allowed_tool_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column(
        "tenant_api_keys",
        sa.Column("allowed_tool_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("tenant_api_keys", "allowed_tool_ids")
    op.drop_column("tenant_api_key_groups", "allowed_tool_ids")
