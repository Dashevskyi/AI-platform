"""voice entitlements + usage metering

Adds per-tenant STT/TTS entitlement flags (licensing gate, default ON so
existing tenants keep working) and a voice_usage table to meter STT (audio
seconds) / TTS (characters) for billing the voice stack as standalone services.

Revision ID: voice01
Revises: actortrust01
Create Date: 2026-06-18
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "voice01"
down_revision: Union[str, None] = "actortrust01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("tenants", sa.Column("stt_enabled", sa.Boolean(), nullable=False, server_default=sa.true()))
    op.add_column("tenants", sa.Column("tts_enabled", sa.Boolean(), nullable=False, server_default=sa.true()))

    op.create_table(
        "voice_usage",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("api_key_id", UUID(as_uuid=True), nullable=True),
        sa.Column("assistant_id", UUID(as_uuid=True), nullable=True),
        sa.Column("chat_id", UUID(as_uuid=True), nullable=True),
        sa.Column("kind", sa.String(10), nullable=False),
        sa.Column("provider", sa.String(40), nullable=True),
        sa.Column("units", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("unit_type", sa.String(10), nullable=False, server_default="chars"),
        sa.Column("cost_usd", sa.Numeric(12, 6), nullable=True),
        sa.Column("success", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_voice_usage_tenant_id", "voice_usage", ["tenant_id"])
    op.create_index("ix_voice_usage_api_key_id", "voice_usage", ["api_key_id"])
    op.create_index("ix_voice_usage_kind", "voice_usage", ["kind"])
    op.create_index("ix_voice_usage_created_at", "voice_usage", ["created_at"])


def downgrade() -> None:
    op.drop_table("voice_usage")
    op.drop_column("tenants", "tts_enabled")
    op.drop_column("tenants", "stt_enabled")
