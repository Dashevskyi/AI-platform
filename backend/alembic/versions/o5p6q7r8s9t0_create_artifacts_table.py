"""create artifacts as a first-class table

Replaces the JSONB-on-messages approach (which kept artifacts coupled to a
specific message and made them invisible once that message fell out of the
context window). Artifacts are now a separate entity with versioning, semantic
embeddings, and soft delete — the immutable source of truth for any code
block, script, SQL, config, document the model produces or the user pastes.

Revision ID: o5p6q7r8s9t0
Revises: n4o5p6q7r8s9
Create Date: 2026-05-15 00:00:00
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector

revision: str = "o5p6q7r8s9t0"
down_revision: Union[str, None] = "n4o5p6q7r8s9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "artifacts",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", sa.dialects.postgresql.UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("chat_id", sa.dialects.postgresql.UUID(as_uuid=True), sa.ForeignKey("chats.id"), nullable=False),
        # The message this artifact was extracted from. SET NULL on delete so
        # the artifact outlives its source — that's the whole point.
        sa.Column(
            "source_message_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("messages.id", ondelete="SET NULL"),
            nullable=True,
        ),
        # Whitelist of well-known kinds + free-form fallback. Normalization
        # lives in the resume_generator extractor.
        sa.Column("kind", sa.String(50), nullable=False),
        sa.Column("label", sa.String(500), nullable=False),
        sa.Column("lang", sa.String(20), nullable=True),
        # The actual content — source of truth. Never modified once written;
        # edits create a new artifact row with parent_artifact_id set.
        sa.Column("content", sa.Text, nullable=False),
        # Rough token estimate for inline-budget calculations in pipeline.
        sa.Column("tokens_estimate", sa.Integer, nullable=False, server_default="0"),
        # Version chain — parent_artifact_id points at the previous revision.
        sa.Column("version", sa.Integer, nullable=False, server_default="1"),
        sa.Column(
            "parent_artifact_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("artifacts.id", ondelete="SET NULL"),
            nullable=True,
        ),
        # Semantic search over labels + content. Embedding model name kept
        # alongside so we can re-embed only on model change.
        sa.Column("embedding", Vector(None), nullable=True),
        sa.Column("embedding_model", sa.String(200), nullable=True),
        # Hot-set tracking — bumped every time an artifact is auto-grounded
        # into a payload. Lets us prioritize recently-used artifacts.
        sa.Column("last_referenced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_artifacts_tenant_chat", "artifacts", ["tenant_id", "chat_id"])
    op.create_index("ix_artifacts_kind", "artifacts", ["kind"])
    op.create_index("ix_artifacts_source_message", "artifacts", ["source_message_id"])
    op.create_index(
        "ix_artifacts_chat_alive_recent",
        "artifacts",
        ["chat_id", "last_referenced_at"],
        postgresql_where=sa.text("deleted_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_artifacts_chat_alive_recent", table_name="artifacts")
    op.drop_index("ix_artifacts_source_message", table_name="artifacts")
    op.drop_index("ix_artifacts_kind", table_name="artifacts")
    op.drop_index("ix_artifacts_tenant_chat", table_name="artifacts")
    op.drop_table("artifacts")
