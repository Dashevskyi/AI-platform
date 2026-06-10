"""background_jobs — durable queue for best-effort enrichment tasks

Replaces fire-and-forget asyncio.create_task for memory extraction, history
summarization and memory embedding. Jobs survive a process restart, retry with
backoff, and are inspectable. NOT under RLS: it's an internal ops table and the
worker claims jobs across all tenants.

revision = 'bgjobs01'
down_revision = 'rls01'
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID, JSONB


revision = 'bgjobs01'
down_revision = 'rls01'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "background_jobs",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", UUID(as_uuid=True),
                  sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=True),
        sa.Column("job_type", sa.String(50), nullable=False),
        sa.Column("payload", JSONB, nullable=False, server_default="{}"),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer, nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer, nullable=False, server_default="5"),
        sa.Column("last_error", sa.Text, nullable=True),
        sa.Column("run_after", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    # Worker poll: cheapest path to "next claimable job".
    op.create_index(
        "ix_background_jobs_claimable", "background_jobs", ["status", "run_after"]
    )


def downgrade() -> None:
    op.drop_index("ix_background_jobs_claimable", table_name="background_jobs")
    op.drop_table("background_jobs")
