"""api key actor_trusted flag

Whether `body.actor` (verified identity) is trusted from a given API key.
Only server-to-server integrations (CRM backend) should be trusted; a
browser/embedded key is attacker-controlled, so its actor is dropped and
forced-filter tools fail closed. Secure default: FALSE.

Revision ID: actortrust01
Revises: schemanote01
Create Date: 2026-06-16
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "actortrust01"
down_revision: Union[str, None] = "schemanote01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "tenant_api_keys",
        sa.Column("actor_trusted", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("tenant_api_keys", "actor_trusted")
