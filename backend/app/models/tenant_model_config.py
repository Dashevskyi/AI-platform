import uuid
from datetime import datetime, timezone

from sqlalchemy import String, Float, DateTime, ForeignKey
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

    # Threshold for complexity classification (0.0 - 1.0)
    complexity_threshold: Mapped[float] = mapped_column(Float, nullable=False, default=0.5)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
