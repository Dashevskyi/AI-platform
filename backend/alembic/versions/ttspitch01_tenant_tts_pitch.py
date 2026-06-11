"""add tts_pitch to tenant_shell_configs

Voice pitch for the Silero TTS provider (SSML prosody pitch):
x-low | low | medium | high | x-high. NULL → model default (medium).

Revision ID: ttspitch01
Revises: hitl01
Create Date: 2026-06-11
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "ttspitch01"
down_revision: Union[str, None] = "hitl01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "tenant_shell_configs",
        sa.Column("tts_pitch", sa.String(length=10), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("tenant_shell_configs", "tts_pitch")
