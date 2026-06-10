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
                "Поиск по корпусу знаний (Knowledge Base) — документация, регламенты, "
                "домен-факты. Вызывай когда нужна справочная информация: инструкции, "
                "процедуры, технические детали, которых нет в памяти или истории чата. "
                "Возвращает title+source+content (до 600 симв на фрагмент)."
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
            "name": "plan",
            "description": (
                "Зарегистрировать план для МНОГОШАГОВОГО запроса. Вызывай ДО "
                "выполнения tools если задача состоит из >1 шага: «проверь A "
                "и B», «найди X, затем покажи Y», «диагностика всей цепочки». "
                "Один tool на простой вопрос — plan НЕ нужен.\n\n"
                "Эффект: план становится артефактом (виден в UI), помогает "
                "тебе самому не сбиться с курса, а пользователь видит что ты "
                "понял задачу. После plan — последовательно выполняй tools "
                "по пунктам. В финальном ответе кратко сверь результаты с планом."
            ),
            "parameters": {
                "type": "object",
                "required": ["steps"],
                "properties": {
                    "steps": {
                        "type": "array",
                        "description": "Шаги в порядке выполнения, 2-8 пунктов. Каждый шаг — короткая фраза-действие («Найти switch_id по адресу через search_addresses», «Снять FDB на порту через switch_command», «Сопоставить mac'и»).",
                        "items": {"type": "string"},
                        "minItems": 2,
                        "maxItems": 8,
                    },
                    "rationale": {
                        "type": "string",
                        "description": "Опциональное обоснование плана 1-2 предложения — почему именно такие шаги и в таком порядке.",
                    },
                },
                "additionalProperties": False,
            },
        },
        "x_backend_config": {"handler": "plan"},
    },
    {
        "type": "function",
        "function": {
            "name": "describe_tool",
            "description": (
                "Получить ПОЛНОЕ описание + parameters schema указанного tool — "
                "используется когда tool упомянут в системном блоке «Доп. tools "
                "(compact)», но в payload-е его полной схемы ещё нет. Возвращает "
                "description, parameters (JSON schema), и hint когда обычно "
                "вызывать. Альтернатива: можешь вызвать tool сразу по имени — "
                "пайплайн добавит схему в payload на следующий раунд. "
                "describe_tool полезнее когда нужны параметры ДО вызова."
            ),
            "parameters": {
                "type": "object",
                "required": ["name"],
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Имя tool из списка «Доп. tools (compact)».",
                    },
                },
                "additionalProperties": False,
            },
        },
        "x_backend_config": {"handler": "describe_tool"},
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


def _apply_description_overrides(tool: dict, overrides: dict[str, str] | None) -> dict:
    """Return a deep-enough copy of `tool` with description swapped if an
    override exists for this tool name. The override only touches the visible
    description — name, parameters, handler stay frozen."""
    if not overrides:
        return tool
    name = tool.get("function", {}).get("name")
    new_desc = overrides.get(name) if name else None
    if not new_desc:
        return tool
    cloned = dict(tool)
    cloned_fn = dict(tool.get("function") or {})
    cloned_fn["description"] = new_desc
    cloned["function"] = cloned_fn
    return cloned


def builtin_tools_for_payload(overrides: dict[str, str] | None = None) -> list[dict]:
    """Return PUBLIC tool definitions (no x_backend_config) ready to attach
    to a chat-completion `tools=[...]` payload. Per-tenant description
    overrides can be passed in (map: tool_name -> description)."""
    out: list[dict] = []
    for t in BUILTIN_TOOLS:
        with_override = _apply_description_overrides(t, overrides)
        d = dict(with_override)
        d.pop("x_backend_config", None)
        out.append(d)
    return out


def builtin_tool_config_map(overrides: dict[str, str] | None = None) -> dict[str, dict]:
    """Return {name: full_config_dict} including x_backend_config — the form
    the executor uses to dispatch handlers. Pipeline copies this and injects
    runtime _context (tenant_id/chat_id/api_key_id). Per-tenant description
    overrides applied if provided."""
    out: dict[str, dict] = {}
    for t in BUILTIN_TOOLS:
        with_override = _apply_description_overrides(t, overrides)
        out[t["function"]["name"]] = dict(with_override)
    return out


def get_builtin_default(tool_name: str) -> dict | None:
    """Return the canonical (un-overridden) registry entry for a builtin
    tool by name, or None if no such tool. Used by the admin endpoint that
    surfaces both the default description and the active override."""
    for t in BUILTIN_TOOLS:
        if t.get("function", {}).get("name") == tool_name:
            return t
    return None
