"""drop builtin tools from tenant_tools — they live in code now

Builtin retrieval/memory/artifact tools moved to
`app/services/tools/builtin_registry.py`. The pipeline always injects them
above the tool-budget cap, and new tenants get them automatically without
running seed migrations. Their per-tenant rows are now dead weight and a
source of confusion (showing up twice if seed migrations run again, taking
slots in admin UI listings).

Revision ID: s9t0u1v2w3x4
Revises: r8s9t0u1v2w3
Create Date: 2026-05-16 00:00:00
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "s9t0u1v2w3x4"
down_revision: Union[str, None] = "r8s9t0u1v2w3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Must match BUILTIN_TOOL_NAMES in app/services/tools/builtin_registry.py
BUILTIN_NAMES = (
    "get_artifact", "find_artifacts", "get_message",
    "recall_chat", "search_kb", "recall_memory", "memory_save",
)


def upgrade() -> None:
    # asyncpg + bindparam(expanding=True) misbehaves on ARRAY casts; spell
    # the list inline as a PostgreSQL array literal — the values are a fixed
    # whitelist of tool names, not user input, so no injection concern.
    names_sql = ", ".join(f"'{n}'" for n in BUILTIN_NAMES)
    op.execute(
        f"""
        DELETE FROM tenant_tools
        WHERE config_json->'function'->>'name' IN ({names_sql})
        """
    )


def downgrade() -> None:
    # Re-seeding would require executing all the previous seed migrations.
    # We don't restore — re-run those seeds manually if needed.
    pass
