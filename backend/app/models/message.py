import uuid
from datetime import datetime, timezone

from sqlalchemy import String, DateTime, Text, Integer, Float, ForeignKey, Index
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column
from pgvector.sqlalchemy import Vector

from app.core.database import Base


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (
        Index("uq_messages_tenant_chat_idempotency", "tenant_id", "chat_id", "idempotency_key", unique=True),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True)
    chat_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("chats.id"), nullable=False, index=True)
    role: Mapped[str] = mapped_column(String(50), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    metadata_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    prompt_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    completion_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    latency_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String(50), default="sent")
    # Resume — short (1-2 sentences) summary of this message used for agentic-memory.
    # Generated in background after assistant reply lands. resume_query is filled
    # on the user-message row, resume_response — on the matching assistant row.
    resume_query: Mapped[str | None] = mapped_column(Text, nullable=True)
    resume_response: Mapped[str | None] = mapped_column(Text, nullable=True)
    resume_embedding = mapped_column(Vector(None), nullable=True)
    resume_embedding_model: Mapped[str | None] = mapped_column(String(200), nullable=True)
    # Structured list of artifacts present in this message: code blocks, scripts,
    # configs, SQL queries, instructions. Each item: {"kind": "...", "label": "...", "lang": "..."}.
    # Content stays in `content`; this column is metadata for retrieval/markers.
    artifacts: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
