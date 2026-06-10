"""memory_entries composite index (tenant_id, chat_id)

Vector (HNSW/ivfflat) indexes are intentionally NOT created here: the embedding
columns are Vector(None) (per-tenant embedding models → variable dimension), and
pgvector indexes require a fixed column dimension. Pinning to vector(1024) would
break multi-model support. At current scale a sequential cosine scan is faster
anyway; revisit (and pin dimension per tenant) when a table exceeds ~10–50k rows.

revision = 'vecidx01'
down_revision = 'tts001config02'
"""
from alembic import op


revision = 'vecidx01'
down_revision = 'tts001config02'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # memory lookups filter by (tenant_id, chat_id); chat_id had no index.
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_memory_entries_tenant_chat "
        "ON memory_entries (tenant_id, chat_id)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_memory_entries_tenant_chat")
