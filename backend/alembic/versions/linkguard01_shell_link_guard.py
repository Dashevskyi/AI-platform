"""tenant shell config link_guard

Deterministic anti-hallucination guard for sensitive (payment) links. A JSONB
config holding sensitive URL patterns + fallback. Tenant-wide default,
overridable per assistant. NULL → guard off (back-compat).

Revision ID: linkguard01
Revises: voice01
Create Date: 2026-06-19
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "linkguard01"
down_revision: Union[str, None] = "voice01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "tenant_shell_configs",
        sa.Column("link_guard", JSONB(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("tenant_shell_configs", "link_guard")
