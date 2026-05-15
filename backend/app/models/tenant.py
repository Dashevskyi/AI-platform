import uuid
from datetime import datetime, timezone

from sqlalchemy import String, Boolean, DateTime, Text, Integer
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class Tenant(Base):
    __tablename__ = "tenants"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(100), unique=True, nullable=False, index=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    throttle_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    throttle_max_concurrent: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    throttle_overflow_policy: Mapped[str] = mapped_column(String(20), nullable=False, default="reject_429")
    throttle_queue_max: Mapped[int] = mapped_column(Integer, nullable=False, default=20)
    merge_messages_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    merge_window_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=1500)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deleted_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
