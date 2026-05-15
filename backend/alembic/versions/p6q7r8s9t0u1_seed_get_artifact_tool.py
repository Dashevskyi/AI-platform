"""seed get_artifact builtin tool + refresh find_artifacts description

Adds the `get_artifact(id)` builtin to every tenant that already has
`find_artifacts` (those are the tenants on the new artifacts architecture).
Also updates find_artifacts description to direct callers to get_artifact for
content retrieval (replacing the old get_message path).

Revision ID: p6q7r8s9t0u1
Revises: o5p6q7r8s9t0
Create Date: 2026-05-15 00:00:00
"""
import json
import uuid
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "p6q7r8s9t0u1"
down_revision: Union[str, None] = "o5p6q7r8s9t0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


GET_ARTIFACT_CONFIG = {
    "type": "function",
    "function": {
        "name": "get_artifact",
        "description": (
            "Получить ДОСЛОВНЫЙ текст артефакта (скрипт, SQL, конфиг, "
            "инструкция) по его id из таблицы артефактов — это источник истины. "
            "Используй ВСЕГДА, когда пользователь спрашивает про детали "
            "содержимого артефакта, помеченного маркером 📎 в Recent conversation, "
            "или возвращённого find_artifacts. НЕ отвечай по резюме — конкретные "
            "значения (IP, числа, имена) бери только из артефакта."
        ),
        "parameters": {
            "type": "object",
            "required": ["id"],
            "properties": {
                "id": {
                    "type": "string",
                    "description": "UUID артефакта (из 📎-маркера в Recent conversation или из find_artifacts).",
                },
            },
            "additionalProperties": False,
        },
    },
    "x_backend_config": {"handler": "get_artifact"},
}


FIND_ARTIFACTS_NEW_DESCRIPTION = (
    "Найти артефакты (скрипты, код, конфиги, SQL-запросы, инструкции) в текущем "
    "чате или (если разрешено) во всём тенанте. Возвращает список с id+kind+label "
    "и similarity. За полным текстом — get_artifact(id). "
    "Используй когда: пользователь упоминает «скрипт», «запрос», «конфиг» а в "
    "Recent conversation подходящего 📎-маркера нет."
)


def upgrade() -> None:
    conn = op.get_bind()

    # 1) Refresh find_artifacts description.
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
            WHERE config_json->'function'->>'name' = 'find_artifacts'
            """
        ),
        {"new_desc": FIND_ARTIFACTS_NEW_DESCRIPTION},
    )

    # 2) Seed get_artifact for every tenant that already has find_artifacts.
    rows = conn.execute(
        sa.text(
            """
            SELECT DISTINCT t.tenant_id, t."group"
            FROM tenant_tools t
            WHERE t.config_json->'function'->>'name' = 'find_artifacts'
              AND t.deleted_at IS NULL
              AND NOT EXISTS (
                  SELECT 1 FROM tenant_tools t2
                  WHERE t2.tenant_id = t.tenant_id
                    AND t2.config_json->'function'->>'name' = 'get_artifact'
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
                "name": "get_artifact",
                "description": GET_ARTIFACT_CONFIG["function"]["description"],
                "config_json": json.dumps(GET_ARTIFACT_CONFIG),
                "group": row.group or "Память",
            },
        )


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(
        sa.text(
            "DELETE FROM tenant_tools WHERE config_json->'function'->>'name' = 'get_artifact'"
        )
    )
