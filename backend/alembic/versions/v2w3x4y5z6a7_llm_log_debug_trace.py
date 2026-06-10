"""add debug JSONB column to llm_request_logs

Temporary instrumentation: collect a per-turn debug snapshot (grounding picks,
tool-call timings, prompt block presence, config snapshot) so we can analyse
the first 100 real chats and identify systemic issues. Will likely be dropped
once the analysis is done.

Revision ID: v2w3x4y5z6a7
Revises: u1v2w3x4y5z6
Create Date: 2026-05-15 23:00:00
"""
from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from alembic import op

revision: str = "v2w3x4y5z6a7"
down_revision: Union[str, None] = "u1v2w3x4y5z6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("llm_request_logs", sa.Column("debug", JSONB, nullable=True))


def downgrade() -> None:
    op.drop_column("llm_request_logs", "debug")
