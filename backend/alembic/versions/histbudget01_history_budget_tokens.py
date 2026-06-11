"""token budget for the prompt history block on tenant_shell_configs

history_budget_tokens — layered history: last pairs verbatim, older pairs
as resumes newest-first while the budget lasts, beyond that the rolling
chat summary.

Revision ID: histbudget01
Revises: voicehold01
Create Date: 2026-06-11
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "histbudget01"
down_revision: Union[str, None] = "voicehold01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "tenant_shell_configs",
        sa.Column("history_budget_tokens", sa.Integer(), nullable=False, server_default="3000"),
    )


def downgrade() -> None:
    op.drop_column("tenant_shell_configs", "history_budget_tokens")
