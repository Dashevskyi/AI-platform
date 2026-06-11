"""voice-mode hold phrase settings on tenant_shell_configs

voice_hold_enabled  — speak filler phrases while the LLM thinks (default on)
voice_hold_delay_ms — delay before the first filler (default 1600)
voice_hold_phrases  — newline-separated custom phrases; NULL → builtins

Revision ID: voicehold01
Revises: ttspitch01
Create Date: 2026-06-11
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "voicehold01"
down_revision: Union[str, None] = "ttspitch01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("tenant_shell_configs", sa.Column("voice_hold_enabled", sa.Boolean(), nullable=True))
    op.add_column("tenant_shell_configs", sa.Column("voice_hold_delay_ms", sa.Integer(), nullable=True))
    op.add_column("tenant_shell_configs", sa.Column("voice_hold_phrases", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("tenant_shell_configs", "voice_hold_phrases")
    op.drop_column("tenant_shell_configs", "voice_hold_delay_ms")
    op.drop_column("tenant_shell_configs", "voice_hold_enabled")
