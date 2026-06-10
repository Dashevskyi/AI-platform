"""kb_inject_auto flag on tenant_shell_configs

Revision ID: dd10ee11ff12
Revises: cc09dd10ee11
Create Date: 2026-06-05

When False: pipeline skips the automatic KB-chunks injection into the system
prompt; the model calls search_kb() on demand. Saves ~1800 tokens per request.
"""
from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "dd10ee11ff12"
down_revision: Union[str, None] = "cc09dd10ee11"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tenant_shell_configs",
        sa.Column(
            "kb_inject_auto",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
    )


def downgrade() -> None:
    op.drop_column("tenant_shell_configs", "kb_inject_auto")
