"""add timezone to tenant_shell_configs

Per-tenant timezone (IANA, e.g. "Europe/Kyiv", "Asia/Tokyo", "UTC"). Used to
render the "current date" system prompt block in the tenant's local time so
"завтра" / "на воскресенье" / "сегодня" computations are correct for that
tenant's locale. Default NULL → fallback to server-local TZ at render time.

Revision ID: z6a7b8c9d0e1
Revises: y5z6a7b8c9d0
Create Date: 2026-05-16 05:30:00
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "z6a7b8c9d0e1"
down_revision: Union[str, None] = "y5z6a7b8c9d0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "tenant_shell_configs",
        sa.Column("timezone", sa.String(60), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("tenant_shell_configs", "timezone")
