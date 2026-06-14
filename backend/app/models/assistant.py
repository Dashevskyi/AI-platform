import uuid
from datetime import datetime, timezone

from sqlalchemy import String, Boolean, DateTime, Text, ForeignKey
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class Assistant(Base):
    """A configurable persona/behaviour profile under a tenant.

    One tenant can run several assistants (voice agent, chat bot, email agent,
    …), each with its own prompt, voice, language, Tier 0, tool scope — while
    sharing the tenant's KB, memory, data sources and billing.

    Behavioural fields are NOT duplicated as columns: `overrides` is a JSONB
    map of TenantShellConfig field name → value. The effective config for a
    request = the tenant's TenantShellConfig with these overrides applied
    (see services/llm/effective_config.py). An empty `overrides` ⇒ identical
    to the tenant default — which is how every existing tenant is migrated
    (one default assistant, `overrides={}`), guaranteeing zero behaviour change.

    `allowed_tool_ids` (NULL = inherit / all) narrows the tool set for this
    assistant, intersected with the API-key allow-list at request time.
    """
    __tablename__ = "assistants"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False, default="Основной")
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Exactly one per tenant should be default; used when a request resolves no
    # explicit assistant (legacy chats, unbound API keys).
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Per-field overrides of the tenant TenantShellConfig. {} = inherit all.
    # NOTE: embedding_model_name is intentionally NOT overridable — KB/memory/
    # tool indexes are shared per tenant and must use one embedding model.
    overrides: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    # Optional tool-scope narrowing for this assistant (NULL = all tenant tools).
    allowed_tool_ids: Mapped[list[str] | None] = mapped_column(JSONB, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
