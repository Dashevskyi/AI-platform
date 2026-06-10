"""pending_tool_actions — human-in-the-loop confirmation for risky tool commands

When a whitelisted command is marked requires_confirmation, the executor does
not run it. Instead it records a pending action here; a human approves/rejects
it via the API, and on approval the command is executed server-side (not by the
LLM). Under RLS like other tenant-scoped tables.

revision = 'hitl01'
down_revision = 'embdim01'
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID, JSONB


revision = 'hitl01'
down_revision = 'embdim01'
branch_labels = None
depends_on = None

_PREDICATE = (
    "NULLIF(current_setting('app.current_tenant', true), '') IS NULL "
    "OR tenant_id = NULLIF(current_setting('app.current_tenant', true), '')::uuid"
)


def upgrade() -> None:
    op.create_table(
        "pending_tool_actions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", UUID(as_uuid=True), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("chat_id", UUID(as_uuid=True), sa.ForeignKey("chats.id", ondelete="CASCADE"), nullable=False),
        sa.Column("message_id", UUID(as_uuid=True), nullable=True),
        sa.Column("tool_name", sa.String(100), nullable=False),
        sa.Column("command_name", sa.String(100), nullable=True),
        sa.Column("command_text", sa.Text, nullable=True),
        sa.Column("arguments", JSONB, nullable=False, server_default="{}"),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("result_text", sa.Text, nullable=True),
        sa.Column("error_text", sa.Text, nullable=True),
        sa.Column("decided_by", sa.String(100), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_pending_tool_actions_chat", "pending_tool_actions", ["chat_id", "status"])
    op.create_index("ix_pending_tool_actions_tenant", "pending_tool_actions", ["tenant_id"])
    # RLS — consistent with other tenant-scoped tables (see rls01).
    op.execute("ALTER TABLE pending_tool_actions ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE pending_tool_actions FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY tenant_isolation ON pending_tool_actions FOR ALL "
        f"USING ({_PREDICATE}) WITH CHECK ({_PREDICATE})"
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON pending_tool_actions")
    op.execute("ALTER TABLE pending_tool_actions NO FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE pending_tool_actions DISABLE ROW LEVEL SECURITY")
    op.drop_table("pending_tool_actions")
