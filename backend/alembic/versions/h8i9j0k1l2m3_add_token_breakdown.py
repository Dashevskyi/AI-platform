"""add token breakdown columns to llm_request_logs

Revision ID: h8i9j0k1l2m3
Revises: g7h8i9j0k1l2
Create Date: 2026-05-14 00:50:00
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "h8i9j0k1l2m3"
down_revision: Union[str, None] = "g7h8i9j0k1l2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    for col in (
        "tokens_system",
        "tokens_tools",
        "tokens_memory",
        "tokens_kb",
        "tokens_history",
        "tokens_user",
    ):
        op.add_column("llm_request_logs", sa.Column(col, sa.Integer(), nullable=True))


def downgrade() -> None:
    for col in (
        "tokens_system",
        "tokens_tools",
        "tokens_memory",
        "tokens_kb",
        "tokens_history",
        "tokens_user",
    ):
        op.drop_column("llm_request_logs", col)
