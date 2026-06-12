"""factual-recall embedding on messages

messages.content_embedding — embedding of the RAW trimmed Q+A text of a
turn (concrete values intact), so recall_chat can match factual queries
("роутер косарева", an IP, a name) that the sanitized resume_embedding
misses. recall_chat returns the resume text regardless — this column is
search-only.

Revision ID: msgcontentemb01
Revises: histbudget01
Create Date: 2026-06-12
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector

revision: str = "msgcontentemb01"
down_revision: Union[str, None] = "histbudget01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

DIM = 1024


def upgrade() -> None:
    op.add_column("messages", sa.Column("content_embedding", Vector(DIM), nullable=True))
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_messages_content_embedding_hnsw "
        "ON messages USING hnsw (content_embedding vector_cosine_ops) "
        "WITH (m = 16, ef_construction = 64)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_messages_content_embedding_hnsw")
    op.drop_column("messages", "content_embedding")
