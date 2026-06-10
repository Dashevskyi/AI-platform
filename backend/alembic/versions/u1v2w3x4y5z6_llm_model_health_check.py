"""add last_check_at + last_check_status to llm_models

A 'model' row used to silently keep `is_active = true` even if the provider
endpoint returned empty content on every call (e.g. wrong model_id). The
result was a dead model whose failure mode was invisible — resume_generator
would log a parse failure deep in the logs and the chat would just go
'context-blind'. The health-check endpoint writes these columns so the UI
can surface the failure right away.

Revision ID: u1v2w3x4y5z6
Revises: t0u1v2w3x4y5
Create Date: 2026-05-15 00:00:00
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "u1v2w3x4y5z6"
down_revision: Union[str, None] = "t0u1v2w3x4y5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("llm_models", sa.Column("last_check_at", sa.DateTime(timezone=True), nullable=True))
    # ok | empty_content | no_completion | http_error | timeout | provider_error | not_checked
    op.add_column("llm_models", sa.Column("last_check_status", sa.String(40), nullable=True))
    # Free-form error message / detail for the last failed check.
    op.add_column("llm_models", sa.Column("last_check_detail", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("llm_models", "last_check_detail")
    op.drop_column("llm_models", "last_check_status")
    op.drop_column("llm_models", "last_check_at")
