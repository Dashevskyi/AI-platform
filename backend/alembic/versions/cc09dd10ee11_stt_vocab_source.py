"""tenant shell config: STT vocab source + fuzzy normalization threshold

Revision ID: cc09dd10ee11
Revises: bb08cc09dd10
Create Date: 2026-06-03
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


revision = "cc09dd10ee11"
down_revision = "bb08cc09dd10"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tenant_shell_configs",
        sa.Column("stt_vocab_source", JSONB(), nullable=True),
    )
    op.add_column(
        "tenant_shell_configs",
        sa.Column("stt_vocab_source_dsn_enc", sa.Text(), nullable=True),
    )
    op.add_column(
        "tenant_shell_configs",
        sa.Column(
            "stt_fuzzy_threshold",
            sa.Float(),
            nullable=False,
            server_default=sa.text("88.0"),
        ),
    )


def downgrade() -> None:
    op.drop_column("tenant_shell_configs", "stt_fuzzy_threshold")
    op.drop_column("tenant_shell_configs", "stt_vocab_source_dsn_enc")
    op.drop_column("tenant_shell_configs", "stt_vocab_source")
