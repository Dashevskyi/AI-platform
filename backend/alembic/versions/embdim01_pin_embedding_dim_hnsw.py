"""Pin embedding columns to vector(1024) and add HNSW indexes

Every embedding in the system is 1024-dim (bge-m3). pgvector requires a fixed
column dimension to build an index, so we pin the columns and create HNSW
indexes (vector_cosine_ops — all similarity search uses cosine_distance). This
keeps similarity search fast as these tables grow.

Constraint introduced: the embedding model must produce 1024-dim vectors
system-wide (bge-m3, bge-large, multilingual-e5-large, ... all qualify). A model
with a different dimension would be rejected at insert by the column type — a
future multi-dimension need would require per-tenant partitioning.

revision = 'embdim01'
down_revision = 'bgjobs01'
"""
from alembic import op


revision = 'embdim01'
down_revision = 'bgjobs01'
branch_labels = None
depends_on = None

DIM = 1024
# (table, column) — every vector column; all are searched by cosine distance.
COLUMNS = [
    ("artifacts", "embedding"),
    ("kb_chunks", "embedding"),
    ("memory_entries", "embedding"),
    ("message_attachment_chunks", "embedding"),
    ("messages", "resume_embedding"),
    ("tenant_tools", "embedding"),
]


def upgrade() -> None:
    for table, col in COLUMNS:
        op.execute(f"ALTER TABLE {table} ALTER COLUMN {col} TYPE vector({DIM}) USING {col}::vector({DIM})")
        op.execute(
            f"CREATE INDEX IF NOT EXISTS ix_{table}_{col}_hnsw "
            f"ON {table} USING hnsw ({col} vector_cosine_ops) "
            f"WITH (m = 16, ef_construction = 64)"
        )


def downgrade() -> None:
    for table, col in COLUMNS:
        op.execute(f"DROP INDEX IF EXISTS ix_{table}_{col}_hnsw")
        op.execute(f"ALTER TABLE {table} ALTER COLUMN {col} TYPE vector USING {col}::vector")
