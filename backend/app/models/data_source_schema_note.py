import uuid
from datetime import datetime, timezone

from sqlalchemy import String, DateTime, Text, ForeignKey, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class DataSourceSchemaNote(Base):
    """A human/agent-authored *semantic* note about a SQL data source's schema.

    Introspection (INFORMATION_SCHEMA) gives the tool-builder the STRUCTURE of a
    data source — real table and column names, types. It does NOT give MEANING:
    that `electric = 1` means "запитка", that `cs.switch_id` points at
    `dev_list.id`, that table `dev_list` is "свичи". This table is that missing
    semantic layer, so the builder agent reads MEANING before it inspects
    structure and stops guessing what columns are *for*.

    Granularity is encoded by which of (table_name, column_name) is set:
      - both NULL                → a source-level note (what this DB is about)
      - table_name set, col NULL → a table-level note (what the table holds)
      - both set                 → a column-level note (what the column means)

    `references` on a column note captures an FK-like relation as a free
    `schema.table.column` string (e.g. "monitoring.dev_list.id") — that's how
    the agent learns which join to make. Notes are seeded automatically from
    already-built tools (their result_columns descriptions + joins already
    encode this) and can be extended by an admin or by the agent itself.
    """
    __tablename__ = "data_source_schema_notes"
    __table_args__ = (
        UniqueConstraint(
            "data_source_id", "table_name", "column_name",
            name="uq_schema_note_ds_table_column",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True)
    data_source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenant_data_sources.id"), nullable=False, index=True,
    )
    # NULL table_name + NULL column_name = source-level note; table set + column
    # NULL = table-level; both set = column-level. Stored normalized ("" → NULL)
    # so the unique constraint dedupes consistently.
    table_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    column_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # FK-like relation target for a column note: "schema.table.column".
    references: Mapped[str | None] = mapped_column(String(512), nullable=True)
    # Where the note came from: 'seed' (auto from tools), 'agent', 'admin'.
    source: Mapped[str] = mapped_column(String(20), nullable=False, default="admin")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
