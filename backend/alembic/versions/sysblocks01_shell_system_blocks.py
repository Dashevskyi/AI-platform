"""tenant shell config system_blocks

Editable override of the static system-prompt blocks (formerly hardcoded
STATIC_SYSTEM_BLOCKS). JSONB list of {label, content, enabled}. NULL → code
defaults (back-compat). Tenant-wide, overridable per assistant.

Revision ID: sysblocks01
Revises: linkguard01
Create Date: 2026-06-21
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "sysblocks01"
down_revision: Union[str, None] = "linkguard01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "tenant_shell_configs",
        sa.Column("system_blocks", JSONB(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("tenant_shell_configs", "system_blocks")
