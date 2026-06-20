import uuid
from datetime import datetime, timezone

from sqlalchemy import String, DateTime, Integer, Numeric, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class VoiceUsage(Base):
    """One STT or TTS call — the metering record for voice billing.

    Voice isn't billed in LLM tokens; the natural units differ per service:
      - TTS → input characters (`units`, unit_type='chars')
      - STT → audio seconds   (`units`, unit_type='seconds')

    `cost_usd` is filled when a per-unit rate is configured (else NULL = volume
    tracked, priced later). Mirrors llm_request_logs but for the voice stack so
    STT/TTS can be metered and sold as standalone services per tenant.
    """
    __tablename__ = "voice_usage"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True)
    api_key_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True, index=True)
    assistant_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    chat_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    kind: Mapped[str] = mapped_column(String(10), nullable=False, index=True)   # 'stt' | 'tts'
    provider: Mapped[str | None] = mapped_column(String(40), nullable=True)     # silero|elevenlabs|fish|whisper|…
    units: Mapped[int] = mapped_column(Integer, nullable=False, default=0)      # chars (tts) / seconds (stt)
    unit_type: Mapped[str] = mapped_column(String(10), nullable=False, default="chars")
    cost_usd: Mapped[float | None] = mapped_column(Numeric(12, 6), nullable=True)
    success: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), index=True,
    )
