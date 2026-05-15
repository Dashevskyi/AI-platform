import uuid
from datetime import datetime, timezone

from sqlalchemy import String, Boolean, DateTime, Text, Float, Integer, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class TenantShellConfig(Base):
    __tablename__ = "tenant_shell_configs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, unique=True, index=True)
    provider_type: Mapped[str] = mapped_column(String(50), nullable=False, default="ollama")
    provider_base_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    provider_api_key_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    model_name: Mapped[str] = mapped_column(String(200), nullable=False, default="qwen2.5:32b")
    system_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Domain ontology: structure of entities, terminology, tool ↔ argument mapping.
    # Separate from system_prompt so admins keep "who you are" and "what data exists" apart.
    ontology_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    rules_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    temperature: Mapped[float] = mapped_column(Float, default=0.7)
    max_context_messages: Mapped[int] = mapped_column(Integer, default=20)
    max_tokens: Mapped[int] = mapped_column(Integer, default=4096)
    summary_model_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    context_mode: Mapped[str] = mapped_column(String(50), default="summary_plus_recent")
    memory_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    knowledge_base_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    embedding_model_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    vision_model_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    kb_max_chunks: Mapped[int] = mapped_column(Integer, default=10)
    tools_policy: Mapped[str] = mapped_column(String(50), default="auto")
    # Qwen3 thinking mode: "on" (always reason), "off" (never), "auto" (heuristic — short/simple → off)
    enable_thinking: Mapped[str] = mapped_column(String(10), default="on")
    # If True, the built-in `recall_chat` tool can search across other chats of the
    # same tenant (default — only the current chat).
    recall_cross_chat_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    # Default language for ALL LLM responses (BCP-47-ish short tag: "ru", "uk", "en", "pl", ...).
    # Used to build a language-pin system message injected into every service call
    # (chat, summary, resume) so the model doesn't switch languages on its own.
    response_language: Mapped[str] = mapped_column(String(8), nullable=False, default="ru")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
