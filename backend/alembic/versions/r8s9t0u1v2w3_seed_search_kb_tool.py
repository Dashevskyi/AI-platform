"""seed search_kb builtin tool

Companion to enabling BLOCK-KB in the pipeline. Auto-grounded KB excerpts
land in the system prompt; this tool lets the model run a wider query when
the auto-grounded set misses the mark.

Revision ID: r8s9t0u1v2w3
Revises: q7r8s9t0u1v2
Create Date: 2026-05-16 00:00:00
"""
import json
import uuid
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "r8s9t0u1v2w3"
down_revision: Union[str, None] = "q7r8s9t0u1v2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


SEARCH_KB_CONFIG = {
    "type": "function",
    "function": {
        "name": "search_kb",
        "description": (
            "Поиск по корпусу знаний (Knowledge Base) tenant'а — документация, "
            "регламенты, домен-факты. Используй когда: (1) релевантной выдержки "
            "нет в системном блоке Knowledge Base, (2) нужна другая формулировка "
            "запроса, (3) нужен более широкий список источников. Возвращает "
            "title+source+content (обрезано до 600 симв)."
        ),
        "parameters": {
            "type": "object",
            "required": ["query"],
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Семантический запрос (1-15 слов).",
                },
                "limit": {
                    "type": "integer",
                    "description": "Сколько фрагментов вернуть (1-15). По умолчанию 5.",
                },
            },
            "additionalProperties": False,
        },
    },
    "x_backend_config": {"handler": "search_kb"},
}


def upgrade() -> None:
    conn = op.get_bind()
    # Seed for every tenant that has KB enabled in their shell config.
    rows = conn.execute(
        sa.text(
            """
            SELECT DISTINCT tenant_id FROM tenant_shell_configs
            WHERE knowledge_base_enabled = true
              AND NOT EXISTS (
                  SELECT 1 FROM tenant_tools t2
                  WHERE t2.tenant_id = tenant_shell_configs.tenant_id
                    AND t2.config_json->'function'->>'name' = 'search_kb'
                    AND t2.deleted_at IS NULL
              )
            """
        )
    ).fetchall()
    for row in rows:
        conn.execute(
            sa.text(
                """
                INSERT INTO tenant_tools (
                    id, tenant_id, name, description, config_json,
                    "group", tool_type, is_active, is_pinned,
                    created_at, updated_at
                ) VALUES (
                    :id, :tenant_id, :name, :description, CAST(:config_json AS jsonb),
                    :group, 'function', true, true, NOW(), NOW()
                )
                """
            ),
            {
                "id": str(uuid.uuid4()),
                "tenant_id": str(row.tenant_id),
                "name": "search_kb",
                "description": SEARCH_KB_CONFIG["function"]["description"],
                "config_json": json.dumps(SEARCH_KB_CONFIG),
                "group": "Память",
            },
        )


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(
        sa.text(
            "DELETE FROM tenant_tools WHERE config_json->'function'->>'name' = 'search_kb'"
        )
    )
