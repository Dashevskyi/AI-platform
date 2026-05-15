"""Built-in tools registry — source of truth for system tools.

These tools (memory / artifacts / RAG / chat-search) are part of the platform
itself, not user-configurable. They live in code, not in `tenant_tools`, so:
  • new tenants get them automatically (no seed migrations needed);
  • their descriptions evolve in PR review, not via DB UPDATEs;
  • they never compete with user-defined tools for the per-request budget —
    pipeline injects them above the budget cap.

Handlers are registered separately in `tools/executor.py` via @register_tool.
This file only carries the OpenAI-style function schema (tool definition) +
the handler name, so the pipeline can publish them to the model.
"""
from __future__ import annotations

from typing import Any


# Order is preserved in the payload — the first one is shown first to the
# model. Kept roughly by frequency-of-use; the model reads top-to-bottom.
BUILTIN_TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "get_artifact",
            "description": (
                "Получить ДОСЛОВНЫЙ текст артефакта (скрипт, SQL, конфиг, инструкция) "
                "по его id из таблицы артефактов — это источник истины. Используй "
                "ВСЕГДА, когда пользователь спрашивает про детали содержимого "
                "артефакта, помеченного маркером 📎 в Recent conversation, или "
                "возвращённого find_artifacts. НЕ отвечай по резюме — конкретные "
                "значения (IP, числа, имена) бери только из артефакта."
            ),
            "parameters": {
                "type": "object",
                "required": ["id"],
                "properties": {
                    "id": {
                        "type": "string",
                        "description": "UUID артефакта (из 📎-маркера или из find_artifacts).",
                    },
                },
                "additionalProperties": False,
            },
        },
        "x_backend_config": {"handler": "get_artifact"},
    },
    {
        "type": "function",
        "function": {
            "name": "find_artifacts",
            "description": (
                "Найти артефакты (скрипты, код, конфиги, SQL-запросы, инструкции) "
                "в текущем чате или (если разрешено) во всём тенанте. Возвращает "
                "список с id+kind+label и similarity. За полным текстом — "
                "get_artifact(id). Используй когда пользователь упоминает «скрипт», "
                "«запрос», «конфиг», а в Recent conversation подходящего 📎-маркера нет."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "kind": {
                        "type": "string",
                        "description": (
                            "Тип артефакта: bash-script, python-script, sql-query, "
                            "yaml-config, json-config, nginx-config, dockerfile, code, "
                            "instruction, document. Опционально — если не указан, ищет все типы."
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
    },
    {
        "type": "function",
        "function": {
            "name": "get_message",
            "description": (
                "Получить ПОЛНОЕ содержимое сообщения (вопрос + ответ ассистента + "
                "список артефактов) по id. Используй когда: (1) в Recent conversation "
                "у обмена есть 📎-маркер артефакта, и пользователь хочет его "
                "изменить/продолжить — ОБЯЗАТЕЛЬНО возьми полный текст; "
                "(2) после recall_chat/find_artifacts когда нужны детали."
            ),
            "parameters": {
                "type": "object",
                "required": ["id"],
                "properties": {
                    "id": {
                        "type": "string",
                        "description": "UUID сообщения (из поля id в результате recall_chat).",
                    },
                },
                "additionalProperties": False,
            },
        },
        "x_backend_config": {"handler": "get_message"},
    },
    {
        "type": "function",
        "function": {
            "name": "recall_chat",
            "description": (
                "Найди в истории прошлые обмены вопрос→ответ, релевантные "
                "текущему запросу. Возвращает краткие резюме с id. Используй "
                "когда пользователь ссылается на «то что обсуждали», «помнишь», "
                "«как раньше делали»."
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
                        "description": "Сколько результатов вернуть (1-20). По умолчанию 5.",
                    },
                    "scope": {
                        "type": "string",
                        "enum": ["chat", "tenant"],
                        "description": "chat — только этот чат (default); tenant — все чаты (если включено политикой).",
                    },
                },
                "additionalProperties": False,
            },
        },
        "x_backend_config": {"handler": "recall_chat"},
    },
    {
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
    },
    {
        "type": "function",
        "function": {
            "name": "recall_memory",
            "description": (
                "Поиск по сохранённым фактам (memory_entries) tenant'а. "
                "Закреплённые (📌 pinned) уже видны в системном промпте — "
                "используй этот tool для всего остального: личных предпочтений "
                "пользователя, истории его настроек, прошлых решений. Возвращает "
                "короткий список id+content+тип+similarity."
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
    },
    {
        "type": "function",
        "function": {
            "name": "memory_save",
            "description": (
                "ВЫЗОВИ когда пользователь говорит запомни/note/remember — для "
                "явного сохранения важной информации в память. content — что "
                "запомнить (короткий факт). memory_type: long_term (по умолчанию) "
                "/ episodic / fact / preference. is_pinned=true только для "
                "критичных правил всегда подмешивать в каждый чат. scope=chat "
                "(только этот чат) / tenant (глобально). НЕ сохраняй данные "
                "клиентов (ФИО/телефон/договор/MAC) — они в БД, доступны через "
                "search_clients. Сохраняй ТОЛЬКО workflow, предпочтения "
                "пользователя, правила работы."
            ),
            "parameters": {
                "type": "object",
                "required": ["content"],
                "properties": {
                    "content": {
                        "type": "string",
                        "description": "Текст для запоминания, до 2000 символов.",
                    },
                    "memory_type": {
                        "type": "string",
                        "enum": ["long_term", "episodic", "fact", "preference"],
                    },
                    "is_pinned": {
                        "type": "boolean",
                        "description": "Закрепить (всегда в контексте).",
                    },
                    "scope": {
                        "type": "string",
                        "enum": ["chat", "tenant"],
                        "description": "chat — только этот чат, tenant — глобально.",
                    },
                    "priority": {
                        "type": "integer",
                        "description": "Приоритет 1-10.",
                    },
                },
                "additionalProperties": False,
            },
        },
        "x_backend_config": {"handler": "memory_save"},
    },
]


BUILTIN_TOOL_NAMES: set[str] = {t["function"]["name"] for t in BUILTIN_TOOLS}


def is_builtin(tool_name: str) -> bool:
    return tool_name in BUILTIN_TOOL_NAMES


def builtin_tools_for_payload() -> list[dict]:
    """Return PUBLIC tool definitions (no x_backend_config) ready to attach
    to a chat-completion `tools=[...]` payload."""
    out: list[dict] = []
    for t in BUILTIN_TOOLS:
        d = dict(t)
        d.pop("x_backend_config", None)
        out.append(d)
    return out


def builtin_tool_config_map() -> dict[str, dict]:
    """Return {name: full_config_dict} including x_backend_config — the form
    the executor uses to dispatch handlers. Pipeline copies this and injects
    runtime _context (tenant_id/chat_id/api_key_id)."""
    return {t["function"]["name"]: dict(t) for t in BUILTIN_TOOLS}
