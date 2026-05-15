"""Artifact model — first-class entity for any reusable content produced or
referenced inside a chat (scripts, SQL, configs, instructions, structured
documents).

Decoupled from the source message: an artifact survives the message falling
out of the context window. Versioning is built in via parent_artifact_id —
edits never mutate; they create a new row. Semantic embedding allows the
pipeline to auto-ground artifacts into payloads based on the user query.
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import String, DateTime, Text, Integer, ForeignKey, Index
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
from pgvector.sqlalchemy import Vector

from app.core.database import Base


class Artifact(Base):
    __tablename__ = "artifacts"
    __table_args__ = (
        Index("ix_artifacts_tenant_chat", "tenant_id", "chat_id"),
        Index("ix_artifacts_kind", "kind"),
        Index("ix_artifacts_source_message", "source_message_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False)
    chat_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("chats.id"), nullable=False)
    # SET NULL preserves the artifact when its source message is hard-deleted.
    source_message_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("messages.id", ondelete="SET NULL"),
        nullable=True,
    )

    kind: Mapped[str] = mapped_column(String(50), nullable=False)
    label: Mapped[str] = mapped_column(String(500), nullable=False)
    lang: Mapped[str | None] = mapped_column(String(20), nullable=True)

    # The actual content — immutable. Edits create new rows.
    content: Mapped[str] = mapped_column(Text, nullable=False)
    tokens_estimate: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Versioning. parent_artifact_id → previous revision (None for v1).
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    parent_artifact_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("artifacts.id", ondelete="SET NULL"),
        nullable=True,
    )

    # Semantic-search index over label + content head. embedding_model is kept
    # so we know whether to re-embed when the tenant changes embedding model.
    embedding = mapped_column(Vector(None), nullable=True)
    embedding_model: Mapped[str | None] = mapped_column(String(200), nullable=True)

    # Touched every time the pipeline auto-grounds this artifact into a payload.
    # Useful for hot-set prioritization and (later) garbage-collection of stale ones.
    last_referenced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
    )
    # Soft delete — the artifact disappears from grounding/UI but the row stays
    # so version chains and message references don't break.
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
