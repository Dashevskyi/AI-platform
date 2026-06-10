"""llm_request_logs.served_by — which tier answered the request

Lets the stats dashboard show the deterministic-vs-LLM split (Tier 0 templates
cost $0 / ~200ms; LLM tiers cost tokens). Tier 0 hits previously wrote no log
row at all, so that traffic was invisible — see pipeline.py.

Values: 'tier0_template' | 'llm'  (room to refine LLM into 14b/72b/cloud later).

revision = 'served01'
down_revision = 'jwtver01'
"""
import sqlalchemy as sa
from alembic import op


revision = 'served01'
down_revision = 'jwtver01'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('llm_request_logs', sa.Column('served_by', sa.String(40), nullable=True))
    # Backfill historical rows: the Tier 0 fast path stamped provider_type='tier0'.
    op.execute("UPDATE llm_request_logs SET served_by = 'tier0_template' WHERE provider_type = 'tier0'")
    op.execute("UPDATE llm_request_logs SET served_by = 'llm' WHERE served_by IS NULL")
    op.create_index('ix_llm_request_logs_served_by', 'llm_request_logs', ['served_by'])


def downgrade() -> None:
    op.drop_index('ix_llm_request_logs_served_by', table_name='llm_request_logs')
    op.drop_column('llm_request_logs', 'served_by')
