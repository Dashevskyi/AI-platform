"""seed recall_memory builtin tool

Adds the `recall_memory` tool to every tenant that already has `memory_save`.
Companion to the BLOCK-MEMORY-B switch: pinned memory items always live in
the system prompt; non-pinned facts are reachable on demand via this tool.

Revision ID: q7r8s9t0u1v2
Revises: p6q7r8s9t0u1
Create Date: 2026-05-16 00:00:00
"""
import json
import uuid
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "q7r8s9t0u1v2"
down_revision: Union[str, None] = "p6q7r8s9t0u1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


RECALL_MEMORY_CONFIG = {
    "type": "function",
    "function": {
        "name": "recall_memory",
        "description": (
            "Поиск по сохранённым фактам (memory_entries) tenant'а. "
            "Закреплённые (📌 pinned) уже видны в системном промпте — используй "
            "этот tool для всего остального: личных предпочтений пользователя, "
            "истории его настроек, прошлых решений. Возвращает короткий список "
            "id+content+тип+similarity."
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
                    "description": "Сколько записей вернуть (1-20). По умолчанию 5.",
                },
                "scope": {
                    "type": "string",
                    "enum": ["chat", "tenant"],
                    "description": "chat — только этот чат (default); tenant — вся память тенанта (если разрешено политикой).",
                },
                "memory_type": {
                    "type": "string",
                    "description": "Опциональный фильтр по типу (long_term / episodic / fact / preference).",
                },
            },
            "additionalProperties": False,
        },
    },
    "x_backend_config": {"handler": "recall_memory"},
}


def upgrade() -> None:
    conn = op.get_bind()
    rows = conn.execute(
        sa.text(
            """
            SELECT DISTINCT t.tenant_id, t."group"
            FROM tenant_tools t
            WHERE t.config_json->'function'->>'name' = 'memory_save'
              AND t.deleted_at IS NULL
              AND NOT EXISTS (
                  SELECT 1 FROM tenant_tools t2
                  WHERE t2.tenant_id = t.tenant_id
                    AND t2.config_json->'function'->>'name' = 'recall_memory'
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
                "name": "recall_memory",
                "description": RECALL_MEMORY_CONFIG["function"]["description"],
                "config_json": json.dumps(RECALL_MEMORY_CONFIG),
                "group": row.group or "Память",
            },
        )


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(
        sa.text(
            "DELETE FROM tenant_tools WHERE config_json->'function'->>'name' = 'recall_memory'"
        )
    )
