import uuid
from datetime import datetime, timezone

from sqlalchemy import String, Float, DateTime, ForeignKey, Integer, Boolean
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class TenantModelConfig(Base):
    """Per-tenant model selection configuration: manual or auto mode."""
    __tablename__ = "tenant_model_configs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, unique=True, index=True)

    mode: Mapped[str] = mapped_column(String(20), nullable=False, default="manual")

    # Manual mode: one model selected
    manual_model_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("llm_models.id", ondelete="SET NULL"), nullable=True)
    manual_custom_model_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("tenant_custom_models.id", ondelete="SET NULL"), nullable=True)

    # Auto mode: light model for simple queries, heavy for complex
    auto_light_model_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("llm_models.id", ondelete="SET NULL"), nullable=True)
    auto_heavy_model_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("llm_models.id", ondelete="SET NULL"), nullable=True)
    auto_light_custom_model_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("tenant_custom_models.id", ondelete="SET NULL"), nullable=True)
    auto_heavy_custom_model_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("tenant_custom_models.id", ondelete="SET NULL"), nullable=True)

    # Threshold for complexity classification (0.0 - 1.0). Legacy — used
    # only when use_complexity_classifier=True.
    complexity_threshold: Mapped[float] = mapped_column(Float, nullable=False, default=0.5)

    # Primary auto-mode trigger: when estimated prompt tokens exceed this
    # threshold → heavy model. Deterministic, runs per round, replaces the
    # complexity-classifier guess. 0 disables size-based routing.
    auto_size_threshold: Mapped[int] = mapped_column(Integer, nullable=False, default=24000)

    # Opt-in for the legacy classifier (extra LLM call per request to
    # estimate complexity from user text). Off by default — size-based
    # routing is more reliable and free.
    use_complexity_classifier: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
