"""Add per-tenant TTS configuration columns

Revision ID: a1b2c3d4e5f6
Revises: z6a7b8c9d0e1
Create Date: 2026-06-05
"""
from alembic import op
import sqlalchemy as sa


revision = 'tts001config02'
down_revision = 'dd10ee11ff12'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('tenant_shell_configs', sa.Column('tts_provider', sa.String(20), nullable=True))
    op.add_column('tenant_shell_configs', sa.Column('tts_api_key_enc', sa.Text(), nullable=True))
    op.add_column('tenant_shell_configs', sa.Column('tts_voice_id', sa.String(200), nullable=True))
    op.add_column('tenant_shell_configs', sa.Column('tts_model', sa.String(200), nullable=True))
    op.add_column('tenant_shell_configs', sa.Column('tts_speed', sa.Float(), nullable=True))
    op.add_column('tenant_shell_configs', sa.Column('tts_fish_url', sa.String(500), nullable=True))


def downgrade() -> None:
    op.drop_column('tenant_shell_configs', 'tts_fish_url')
    op.drop_column('tenant_shell_configs', 'tts_speed')
    op.drop_column('tenant_shell_configs', 'tts_model')
    op.drop_column('tenant_shell_configs', 'tts_voice_id')
    op.drop_column('tenant_shell_configs', 'tts_api_key_enc')
    op.drop_column('tenant_shell_configs', 'tts_provider')
