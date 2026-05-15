"""add ontology_prompt to tenant_shell_configs

Revision ID: j0k1l2m3n4o5
Revises: i9j0k1l2m3n4
Create Date: 2026-05-14 23:30:00
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "j0k1l2m3n4o5"
down_revision: Union[str, None] = "i9j0k1l2m3n4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("tenant_shell_configs", sa.Column("ontology_prompt", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("tenant_shell_configs", "ontology_prompt")
