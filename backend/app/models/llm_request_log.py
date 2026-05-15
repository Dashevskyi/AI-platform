import uuid
from datetime import datetime, timezone

from sqlalchemy import String, DateTime, Text, Integer, Float, ForeignKey
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class LLMRequestLog(Base):
    __tablename__ = "llm_request_logs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True)
    chat_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("chats.id"), nullable=True, index=True)
    api_key_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("tenant_api_keys.id"), nullable=True, index=True)
    message_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("messages.id"), nullable=True)
    correlation_id: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    provider_type: Mapped[str] = mapped_column(String(50), nullable=False)
    model_name: Mapped[str] = mapped_column(String(200), nullable=False)
    raw_request: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    raw_response: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    normalized_request: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    normalized_response: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    status: Mapped[str] = mapped_column(String(50), default="success")
    error_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    latency_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    time_to_first_token_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    prompt_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    completion_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    request_size_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    response_size_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tool_calls_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    finish_reason: Mapped[str | None] = mapped_column(String(50), nullable=True)
    estimated_cost: Mapped[float | None] = mapped_column(Float, nullable=True)
    context_messages_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    context_memory_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    context_kb_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    context_tools_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Per-section token breakdown (approx via tiktoken cl100k_base, accurate ±10%)
    tokens_system: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tokens_tools: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tokens_memory: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tokens_kb: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tokens_history: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tokens_user: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
