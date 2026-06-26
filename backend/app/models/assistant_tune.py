"""Auto-tuning recommendations for an assistant's tool routing.

A read-only analysis loop: the audit cases are run against the LIGHT model, the
failures are classified, and a HEAVY model diagnoses each and proposes a concrete
config change (tool description / param description / arg_formats coercion / usage
example / tier0 route / assistant ontology). Recommendations are STAGED here —
nothing touches the live tool/assistant config until an admin clicks "Apply" on a
specific row. This keeps the saved parameters untouched during analysis.
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class AssistantTuneRecommendation(Base):
    __tablename__ = "assistant_tune_recommendations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True, nullable=False)
    assistant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True, nullable=False)
    run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)

    # scope: 'tool' (tenant-wide config_json — affects every assistant using the
    # tool) or 'assistant' (assistant.overrides — scoped to this assistant only).
    scope: Mapped[str] = mapped_column(Text, nullable=False, default="tool")
    tool_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    tool_name: Mapped[str | None] = mapped_column(Text, nullable=True)

    # change_type ∈ description | param_description | arg_format | usage_example
    #            | capability_tag | tier0 | ontology
    change_type: Mapped[str] = mapped_column(Text, nullable=False)
    json_path: Mapped[str | None] = mapped_column(Text, nullable=True)   # human-readable target path
    param_name: Mapped[str | None] = mapped_column(Text, nullable=True)  # for param_description / arg_format

    current_value: Mapped[dict | list | str | None] = mapped_column(JSONB, nullable=True)
    proposed_value: Mapped[dict | list | str | None] = mapped_column(JSONB, nullable=True)
    rationale: Mapped[str | None] = mapped_column(Text, nullable=True)
    # True for deterministic levers (arg_format coercion, tier0 route) — preferred
    # over probabilistic description tweaks (thick-tools / deterministic-first).
    deterministic: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    failing_case_ids: Mapped[list | None] = mapped_column(JSONB, nullable=True)

    status: Mapped[str] = mapped_column(Text, nullable=False, default="pending")  # pending|applied|dismissed
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
