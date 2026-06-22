"""Saved tool-routing audit suite per assistant.

A persistent set of test cases (question + expected tool call order + actor) that
an admin runs before releasing an assistant to a client. Each run records a
snapshot for the pass-rate trend; per-case `last_result` caches the latest verdict
so the table renders without re-running.
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class AssistantAuditCase(Base):
    __tablename__ = "assistant_audit_cases"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True, nullable=False)
    assistant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    # Ordered list of expected tool names (call order for multi-round). An item
    # may be "a|b" for any-of. Empty list = no tool expected (conversational).
    expected_tools: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    actor: Mapped[dict | None] = mapped_column(JSONB, nullable=True)   # {role, external_id, phone}
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    order_index: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # Cached latest run: {passed, pass_rate, repeats, called, tier0, latency_ms,
    # tokens, ts} — so the table shows a verdict without a fresh run.
    last_result: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc))


class AssistantAuditRun(Base):
    __tablename__ = "assistant_audit_runs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True, nullable=False)
    assistant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True, nullable=False)
    total: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    passed: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # {by_tool: {tool: {misses, share}}, recommendations: [...], model: "..."}
    summary: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
