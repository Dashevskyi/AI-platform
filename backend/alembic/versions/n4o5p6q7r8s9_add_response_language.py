"""add response_language to tenant_shell_configs

Drives the language used for ALL service LLM calls (summary, resume, etc).
Default 'ru' since current tenants are Russian-speaking.

Revision ID: n4o5p6q7r8s9
Revises: m3n4o5p6q7r8
Create Date: 2026-05-15 00:00:00
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "n4o5p6q7r8s9"
down_revision: Union[str, None] = "m3n4o5p6q7r8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "tenant_shell_configs",
        sa.Column(
            "response_language",
            sa.String(8),
            nullable=False,
            server_default=sa.text("'ru'"),
        ),
    )


def downgrade() -> None:
    op.drop_column("tenant_shell_configs", "response_language")
