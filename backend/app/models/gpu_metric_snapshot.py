import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class GPUMetricSnapshot(Base):
    __tablename__ = "gpu_metric_snapshots"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), index=True, default=lambda: datetime.now(timezone.utc)
    )
    # gpus: list[{idx, uuid, name, util_pct, memory_used_bytes, memory_total_bytes, temperature_c, power_w}]
    gpus: Mapped[list] = mapped_column(JSONB, default=list)
    # vllm: {running, waiting, kv_cache_usage, prompt_tokens_total, generation_tokens_total, prefix_cache_hit_rate}
    vllm: Mapped[dict | None] = mapped_column(JSONB, default=None)
