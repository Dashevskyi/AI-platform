"""seed find_artifacts builtin tool + refresh get_message description

Adds the `find_artifacts` builtin to every tenant that already has `recall_chat`,
so agentic-memory-enabled tenants gain artifact retrieval automatically. Also
refreshes the description of `get_message` to mention 📎 markers and artifacts.

Revision ID: m3n4o5p6q7r8
Revises: l2m3n4o5p6q7
Create Date: 2026-05-14 00:00:00
"""
import json
import uuid
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "m3n4o5p6q7r8"
down_revision: Union[str, None] = "l2m3n4o5p6q7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


FIND_ARTIFACTS_CONFIG = {
    "type": "function",
    "function": {
        "name": "find_artifacts",
        "description": (
            "Найди в истории артефакты: скрипты, код, конфиги, SQL-запросы, инструкции. "
            "Используй когда пользователь хочет ИЗМЕНИТЬ/ПРОДОЛЖИТЬ артефакт "
            "(«добавь в скрипт», «исправь запрос», «доработай конфиг»), а в видимом резюме "
            "нет подходящего 📎-маркера. Возвращает id+kind+label; за полным текстом — get_message(id)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "kind": {
                    "type": "string",
                    "description": (
                        "Тип артефакта: bash-script, python-script, sql-query, "
                        "yaml-config, json-config, nginx-config, dockerfile, code, instruction, document. "
                        "Опционально — если не указан, ищет все типы."
                    ),
                },
                "query": {
                    "type": "string",
                    "description": "Семантический запрос для ранжирования по similarity (опционально, 1-10 слов).",
                },
                "limit": {
                    "type": "integer",
                    "description": "Сколько результатов вернуть (1-30). По умолчанию 10.",
                },
                "scope": {
                    "type": "string",
                    "enum": ["chat", "tenant"],
                    "description": "chat — только этот чат (default); tenant — все чаты (если разрешено).",
                },
            },
            "additionalProperties": False,
        },
    },
    "x_backend_config": {"handler": "find_artifacts"},
}


GET_MESSAGE_NEW_DESCRIPTION = (
    "Получить ПОЛНОЕ содержимое сообщения (вопрос + ответ ассистента + список артефактов) по id. "
    "Используй когда: (1) в Recent conversation у обмена есть 📎-маркер артефакта, и пользователь "
    "хочет его изменить/продолжить — ОБЯЗАТЕЛЬНО возьми полный текст; "
    "(2) после recall_chat/find_artifacts когда нужны детали."
)


def upgrade() -> None:
    conn = op.get_bind()
    now_sql = sa.text("NOW()")

    # 1) Update get_message description for all tenants that have it.
    conn.execute(
        sa.text(
            """
            UPDATE tenant_tools
            SET config_json = jsonb_set(
                    config_json,
                    '{function,description}',
                    to_jsonb(CAST(:new_desc AS text)),
                    true
                ),
                updated_at = NOW()
            WHERE config_json->'function'->>'name' = 'get_message'
            """
        ),
        {"new_desc": GET_MESSAGE_NEW_DESCRIPTION},
    )

    # 2) Seed find_artifacts for every tenant that already has recall_chat
    #    (those are the tenants where agentic memory is in use).
    rows = conn.execute(
        sa.text(
            """
            SELECT DISTINCT t.tenant_id, t."group"
            FROM tenant_tools t
            WHERE t.config_json->'function'->>'name' = 'recall_chat'
              AND t.deleted_at IS NULL
              AND NOT EXISTS (
                  SELECT 1 FROM tenant_tools t2
                  WHERE t2.tenant_id = t.tenant_id
                    AND t2.config_json->'function'->>'name' = 'find_artifacts'
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
                    :group, 'function', true, true,
                    NOW(), NOW()
                )
                """
            ),
            {
                "id": str(uuid.uuid4()),
                "tenant_id": str(row.tenant_id),
                "name": "find_artifacts",
                "description": FIND_ARTIFACTS_CONFIG["function"]["description"],
                "config_json": json.dumps(FIND_ARTIFACTS_CONFIG),
                "group": row.group or "Память",
            },
        )


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(
        sa.text(
            "DELETE FROM tenant_tools "
            "WHERE config_json->'function'->>'name' = 'find_artifacts'"
        )
    )
    # We don't revert the get_message description — non-destructive change.
