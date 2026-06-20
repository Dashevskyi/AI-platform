"""Conversational tool-builder agent.

An admin describes, in chat, the tool they want ("создай тул поиска запитки на
свиче"). A heavy model drives the build: it inspects the REAL schema of a data
source, dry-runs candidate SQL, asks clarifying questions, and finally proposes
a complete `search_records` tool config — which the admin reviews and saves.

Why an agent and not a one-shot generator: the failure we kept hitting (guessing
`switch_name` instead of `d.Name`, MySQL alias-in-WHERE, wrong required_fields
path) all come from NOT looking at the real schema. Give the model eyes
(introspection) + a way to verify (dry-run) and it stops guessing.

Strictly read-only during the conversation; tool creation is a separate,
explicit admin action and the new tool is created DISABLED.
"""
import json
import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.api.deps import require_role, require_tenant_access, require_permission
from app.models.tenant_data_source import TenantDataSource
from app.models.tenant_tool import TenantTool
from app.models.tenant_shell_config import TenantShellConfig

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/admin/tenants/{tenant_id}/tool-builder",
    tags=["admin-tool-builder"],
    dependencies=[
        Depends(require_role("superadmin", "tenant_admin")),
        Depends(require_tenant_access),
        Depends(require_permission("tools")),
    ],
)

# A cautious agent spends rounds reading the schema notes AND introspecting
# structure (tables → columns → dry-runs) before it can propose. When the model
# issues one call per round this adds up; 16 gives headroom, and even if it runs
# out, a final salvage turn (below) still proposes-or-asks instead of dead-ending.
MAX_TOOL_ROUNDS = 16

_SYSTEM_PROMPT = """Ты — ассистент-конструктор инструментов (tools) для AI-платформы.
Админ описывает на естественном языке, какой инструмент нужен; ты собираешь его,
ОПИРАЯСЬ НА РЕАЛЬНУЮ СХЕМУ БД, и в конце предлагаешь готовый конфиг через
`propose_tool`. Отвечай по-русски, кратко и по делу.

ЖЕЛЕЗНЫЕ ПРАВИЛА:
0. У каждого источника есть СПРАВОЧНИК СМЫСЛА (понятийный слой): что значат
   таблицы/колонки и какие между ними связи (FK). Интроспекция даёт СТРУКТУРУ
   (имена/типы), справочник — СМЫСЛ. Выбрав источник, СНАЧАЛА вызови
   `get_schema_notes` — это подскажет нужные таблицы/колонки/джойны и сэкономит
   шаги. Если по ходу разобрался в смысле колонки/связи, которой нет в
   справочнике, — добавь её через `save_schema_note`, чтобы в следующий раз не
   гадать (особенно неочевидное: «electric=1 = запитка», «cs.switch_id → dev_list.id»).
1. НИКОГДА не угадывай имена таблиц/колонок. После справочника: `list_tables`,
   затем `list_columns` — сверь РЕАЛЬНЫЕ имена и типы. Не трать ходы зря: если
   нужны несколько таблиц/колонок — запрашивай их за ОДИН ход (несколько
   tool-вызовов сразу), не по одному. Чем меньше ходов до dry_run и
   `propose_tool` — тем лучше.
2. Перед `propose_tool` ОБЯЗАТЕЛЬНО проверь запрос через `dry_run_sql` — он должен
   вернуть строки на реальном примере. Если ошибка БД — читай её и исправляй.
3. Если данных для решения не хватает (какие примеры запросов, что показывать в
   результате, какая таблица базовая) — задай админу уточняющий вопрос, не выдумывай.
4. Базовую таблицу выбирай по смыслу поиска: ищем «на свиче» → базовая = таблица
   свичей; джойнами подтягивай остальное (LEFT JOIN, чтобы записи без связи не
   терялись).

Инструмент типа `search_records` (handler=search_records) описывается конфигом:
- `table` (схема.таблица базовой), опц. `table_alias`
- `joins`: [{type:"left", alias, table, left_column, right_column}]
- `search_columns`: список РЕАЛЬНЫХ колонок (или выражений) для свободного поиска —
  НЕ алиасы из SELECT (MySQL не пускает алиасы в WHERE!)
- `result_columns`: [{alias, column, description}] — column это реальное выражение,
  alias — как назвать в выводе
- `filter_fields`: {имя_фильтра: {mode:"eq|contains|exact", column:"реальная.колонка"}} —
  можно ОПУСТИТЬ (пустой объект), если поиск только по свободному `query` через
  `search_columns`. Нужен ХОТЯ БЫ один способ сузить выборку: filter_fields ИЛИ search_columns.
- `static_filters`: {колонка: значение} — всегда подставляемые условия
- `default_limit`, `max_limit`
- `sort_by`: строка-колонка ("cs.id"), "cs.id desc", или список
  [{column:"dl.changed", direction:"desc"}] для «последних N». По умолчанию — по возрастанию.

ВАЖНО про MySQL: фильтровать можно только по реальным колонкам/выражениям, не по
алиасам из SELECT.

Если уместен Tier 0 (детерминированный ответ без LLM на типовой запрос), можешь
включить его в конфиг (`tier0_template`): keyword_regex с ОДНИМ обязательным
триггер-словом (основа слова, не один падеж: запитк[ауиі]), опциональными
коннекторами; required_fields как ПУТЬ в результат (напр. items.0.switch_name);
template со {плейсхолдерами.путь}. Но это опционально — спроси админа, нужен ли.

Когда всё проверено dry_run'ом — вызови `propose_tool` с полным config_json.
"""


def _agent_tools() -> list[dict]:
    return [
        {"type": "function", "function": {
            "name": "list_data_sources",
            "description": "Список источников данных тенанта (id, имя, тип). Начни с него.",
            "parameters": {"type": "object", "properties": {}},
        }},
        {"type": "function", "function": {
            "name": "get_schema_notes",
            "description": "Справочник СМЫСЛА источника: что значат таблицы/колонки и связи между ними (FK). Вызывай ПЕРВЫМ после выбора источника — экономит шаги интроспекции.",
            "parameters": {"type": "object", "required": ["data_source_id"], "properties": {
                "data_source_id": {"type": "string"},
            }},
        }},
        {"type": "function", "function": {
            "name": "save_schema_note",
            "description": "Записать в справочник смысл колонки/таблицы или связь (FK), которую ты понял. column опускай для заметки о таблице. references — цель связи как 'schema.table.column'.",
            "parameters": {"type": "object", "required": ["data_source_id", "table"], "properties": {
                "data_source_id": {"type": "string"},
                "table": {"type": "string", "description": "реальное имя таблицы (schema.table)"},
                "column": {"type": "string", "description": "имя колонки; опусти для заметки о таблице"},
                "description": {"type": "string", "description": "что значит таблица/колонка"},
                "references": {"type": "string", "description": "цель FK-связи: 'schema.table.column'"},
            }},
        }},
        {"type": "function", "function": {
            "name": "list_tables",
            "description": "Базовые таблицы SQL-источника данных.",
            "parameters": {"type": "object", "required": ["data_source_id"], "properties": {
                "data_source_id": {"type": "string"},
            }},
        }},
        {"type": "function", "function": {
            "name": "list_columns",
            "description": "Колонки таблицы (имя, тип, nullable). Таблица как 'schema.table' или 'table'.",
            "parameters": {"type": "object", "required": ["data_source_id", "table"], "properties": {
                "data_source_id": {"type": "string"},
                "table": {"type": "string"},
            }},
        }},
        {"type": "function", "function": {
            "name": "dry_run_sql",
            "description": "Прогнать ОДИН SELECT (read-only, до 5 строк) на источнике — проверить, что запрос валиден и что-то возвращает. Возвращает строки или текст ошибки БД.",
            "parameters": {"type": "object", "required": ["data_source_id", "sql"], "properties": {
                "data_source_id": {"type": "string"},
                "sql": {"type": "string"},
            }},
        }},
        {"type": "function", "function": {
            "name": "propose_tool",
            "description": "Предложить готовый инструмент админу на проверку (после успешного dry_run). НЕ создаёт его сразу — админ подтверждает.",
            "parameters": {"type": "object", "required": ["name", "description", "config_json"], "properties": {
                "name": {"type": "string", "description": "machine-имя инструмента (латиница, snake_case)"},
                "description": {"type": "string", "description": "что делает инструмент (для модели)"},
                "config_json": {"type": "object", "description": "полный config_json: function{name,description,parameters} + x_backend_config{handler:'search_records', data_source_id, table, joins, search_columns, result_columns, filter_fields, static_filters, default_limit, max_limit, sort_by, [tier0_template]}"},
            }},
        }},
    ]


async def _exec_agent_tool(name: str, args: dict, tenant_id: uuid.UUID, db: AsyncSession) -> str:
    """Execute one agent tool, return a JSON string for the model."""
    from app.api.admin.data_introspect import _db_url_for, _run, DryRunRequest, dry_run_sql
    from app.api.admin.schema_notes import (
        list_notes, build_digest, upsert_note,
    )

    try:
        if name == "get_schema_notes":
            notes = await list_notes(db, uuid.UUID(args["data_source_id"]))
            return build_digest(notes)

        if name == "save_schema_note":
            note = await upsert_note(
                db, tenant_id, uuid.UUID(args["data_source_id"]),
                args.get("table"), args.get("column"),
                description=args.get("description"), references=args.get("references"),
                source="agent", fill_only=False,
            )
            return json.dumps({"ok": True, "table": note.table_name,
                               "column": note.column_name}, ensure_ascii=False)

        if name == "list_data_sources":
            rows = (await db.execute(
                select(TenantDataSource).where(
                    TenantDataSource.tenant_id == tenant_id,
                    TenantDataSource.deleted_at.is_(None),
                    TenantDataSource.is_active.is_(True),
                )
            )).scalars().all()
            return json.dumps([
                {"id": str(d.id), "name": d.name, "kind": d.kind}
                for d in rows
            ], ensure_ascii=False)

        if name == "list_tables":
            db_url, _k = await _db_url_for(args["data_source_id"])
            sql = ("SELECT table_schema, table_name FROM information_schema.tables "
                   "WHERE table_type='BASE TABLE' ORDER BY table_schema, table_name")
            rows = await _run(db_url, sql, {})
            sysv = {"information_schema", "performance_schema", "mysql", "sys", "pg_catalog", "pg_toast"}
            out = [f"{(r.get('table_schema') or r.get('TABLE_SCHEMA'))}.{(r.get('table_name') or r.get('TABLE_NAME'))}"
                   for r in rows if (r.get('table_schema') or r.get('TABLE_SCHEMA')) not in sysv]
            return json.dumps({"tables": out[:500]}, ensure_ascii=False)

        if name == "list_columns":
            db_url, _k = await _db_url_for(args["data_source_id"])
            tbl = args["table"].strip(); schema = None
            if "." in tbl:
                schema, tbl = tbl.split(".", 1)
            where = "table_name = :t"; params = {"t": tbl}
            if schema:
                where += " AND table_schema = :s"; params["s"] = schema
            sql = (f"SELECT column_name, data_type, is_nullable FROM information_schema.columns "
                   f"WHERE {where} ORDER BY ordinal_position")
            rows = await _run(db_url, sql, params)
            cols = [{"name": r.get('column_name') or r.get('COLUMN_NAME'),
                     "type": r.get('data_type') or r.get('DATA_TYPE'),
                     "nullable": (r.get('is_nullable') or r.get('IS_NULLABLE')) == "YES"} for r in rows]
            return json.dumps({"table": args["table"], "columns": cols}, ensure_ascii=False)

        if name == "dry_run_sql":
            res = await dry_run_sql(tenant_id, args["data_source_id"], DryRunRequest(sql=args["sql"], limit=5))
            return json.dumps(res, ensure_ascii=False, default=str)[:4000]

        if name == "propose_tool":
            # Signaled to the loop via a sentinel; handled by the caller.
            return "__PROPOSE__"

        return json.dumps({"error": f"unknown tool {name}"}, ensure_ascii=False)
    except HTTPException as he:
        return json.dumps({"error": he.detail}, ensure_ascii=False)
    except Exception as e:
        logger.exception("tool-builder tool %s failed", name)
        return json.dumps({"error": str(e)[:500]}, ensure_ascii=False)


async def _resolve_heavy_model(tenant_id: str, db: AsyncSession, cfg):
    """Pick the tenant's HEAVY model for agent reasoning: auto_heavy → manual →
    light → shell-config fallback. Tool-building needs strong function-calling,
    not the light/local model."""
    from app.models.tenant_model_config import TenantModelConfig
    from app.services.llm.model_resolver import (
        _load_model_record, _make_provider, _resolve_from_shell_config,
    )
    mc = (await db.execute(
        select(TenantModelConfig).where(TenantModelConfig.tenant_id == uuid.UUID(tenant_id))
    )).scalar_one_or_none()
    if mc:
        for mid, cid in (
            (mc.auto_heavy_model_id, mc.auto_heavy_custom_model_id if hasattr(mc, "auto_heavy_custom_model_id") else None),
            (mc.manual_model_id, mc.manual_custom_model_id),
            (mc.auto_light_model_id, mc.auto_light_custom_model_id),
        ):
            if mid or cid:
                try:
                    record, is_custom = await _load_model_record(mid, cid, db)
                    if record:
                        return _make_provider(record, is_custom)
                except Exception:
                    continue
    return _resolve_from_shell_config(cfg)


class BuilderChatRequest(BaseModel):
    messages: list[dict]  # [{role, content}] — full history from the UI


@router.post("/chat")
async def builder_chat(
    tenant_id: uuid.UUID, body: BuilderChatRequest, db: AsyncSession = Depends(get_db),
) -> dict:
    """Run one builder turn: the agent may introspect/dry-run several times, then
    either ask a question (text) or propose a tool config. Stateless — the UI
    sends the full message history each turn."""
    cfg = (await db.execute(
        select(TenantShellConfig).where(TenantShellConfig.tenant_id == tenant_id)
    )).scalar_one_or_none()
    if not cfg:
        raise HTTPException(404, "Tenant config not found")

    resolved = await _resolve_heavy_model(str(tenant_id), db, cfg)
    provider = resolved.provider
    # Disable thinking — function-calling agents need content/tool_calls, not a
    # <think> dump that some local models leave content empty after.
    _no_think = {"chat_template_kwargs": {"enable_thinking": False, "thinking": False}}

    messages: list[dict] = [{"role": "system", "content": _SYSTEM_PROMPT}]
    messages += [m for m in body.messages if m.get("role") in ("user", "assistant", "tool")]

    tools = _agent_tools()
    trace: list[dict] = []
    proposed: dict | None = None

    for _round in range(MAX_TOOL_ROUNDS):
        resp = await provider.chat_completion(
            messages=messages, model=resolved.model_name,
            temperature=0.2, max_tokens=cfg.max_tokens or 4096, tools=tools, extra_body=_no_think,
        )
        if not resp.tool_calls:
            return {"reply": resp.content or "", "trace": trace, "proposed": proposed}

        messages.append(provider.format_assistant_turn(resp))
        for tc in resp.tool_calls:
            fn = (tc.get("function") or tc) if isinstance(tc, dict) else {}
            tname = fn.get("name", "")
            try:
                targs = fn.get("arguments") or {}
                if isinstance(targs, str):
                    targs = json.loads(targs or "{}")
            except json.JSONDecodeError:
                targs = {}
            tcid = tc.get("id") if isinstance(tc, dict) else None

            if tname == "propose_tool":
                proposed = {
                    "name": targs.get("name"),
                    "description": targs.get("description"),
                    "config_json": targs.get("config_json"),
                }
                out = "Предложение принято — показываю админу на подтверждение."
            else:
                out = await _exec_agent_tool(tname, targs, tenant_id, db)
            trace.append({"tool": tname, "args": targs, "result_preview": out[:300]})
            messages.append(provider.format_tool_result_turn(tool_call_id=tcid, content=out))

        if proposed is not None:
            # Ask the model for a short closing message to show with the proposal.
            resp2 = await provider.chat_completion(
                messages=messages, model=resolved.model_name,
                temperature=0.2, max_tokens=cfg.max_tokens or 1024, tools=None, extra_body=_no_think,
            )
            return {"reply": resp2.content or "Готов конфиг инструмента — проверь и сохрани.",
                    "trace": trace, "proposed": proposed}

    # Out of tool-exploration rounds without a proposal. Give the model ONE
    # final turn — no more introspection — to either propose with what it has
    # or tell the admin, in plain language, what it understood and the single
    # thing it still needs. Replaces a dead-end "limit reached" string with
    # something a non-technical user can act on.
    messages.append({"role": "user", "content": (
        "Шаги на изучение схемы закончились. Больше НЕ вызывай list_data_sources/"
        "list_tables/list_columns/dry_run_sql/get_schema_notes/save_schema_note. "
        "Если уже собрал достаточно — вызови propose_tool с готовым конфигом. "
        "Если данных не хватило — НЕ вызывай ничего, просто ответь мне обычными "
        "словами по-русски, без технического жаргона: что ты уже понял по задаче "
        "и какой ОДИН вопрос мне задать, чтобы довести инструмент до конца. "
        "НЕ упоминай шаги, лимиты и «инструменты изучения» — обратись ко мне "
        "естественно, как помощник, который хочет уточнить задачу."
    )})
    final_tools = [t for t in tools if t.get("function", {}).get("name") == "propose_tool"]
    resp = await provider.chat_completion(
        messages=messages, model=resolved.model_name,
        temperature=0.3, max_tokens=cfg.max_tokens or 2048,
        tools=final_tools, extra_body=_no_think,
    )
    if resp.tool_calls:
        messages.append(provider.format_assistant_turn(resp))
        for tc in resp.tool_calls:
            fn = (tc.get("function") or tc) if isinstance(tc, dict) else {}
            if fn.get("name") != "propose_tool":
                continue
            try:
                targs = fn.get("arguments") or {}
                if isinstance(targs, str):
                    targs = json.loads(targs or "{}")
            except json.JSONDecodeError:
                targs = {}
            proposed = {
                "name": targs.get("name"),
                "description": targs.get("description"),
                "config_json": targs.get("config_json"),
            }
            trace.append({"tool": "propose_tool", "args": targs, "result_preview": "финальный ход"})
            messages.append(provider.format_tool_result_turn(
                tool_call_id=(tc.get("id") if isinstance(tc, dict) else None),
                content="Предложение принято — показываю админу на подтверждение."))
        if proposed is not None:
            resp2 = await provider.chat_completion(
                messages=messages, model=resolved.model_name,
                temperature=0.2, max_tokens=cfg.max_tokens or 1024, tools=None, extra_body=_no_think,
            )
            return {"reply": resp2.content or "Готов конфиг инструмента — проверь и сохрани.",
                    "trace": trace, "proposed": proposed}

    fallback = (
        "Пока не успел собрать инструмент целиком. Опишите, пожалуйста, чуть "
        "конкретнее: какие данные нужно искать, по какому полю (например, по "
        "имени, номеру договора, адресу) и что показать в ответе — и я продолжу."
    )
    return {"reply": resp.content or fallback, "trace": trace, "proposed": proposed}


class CreateProposedRequest(BaseModel):
    name: str
    description: str | None = None
    config_json: dict
    is_active: bool = False  # created DISABLED by default — admin enables after review


@router.post("/create")
async def create_proposed_tool(
    tenant_id: uuid.UUID, body: CreateProposedRequest, db: AsyncSession = Depends(get_db),
) -> dict:
    """Persist an agent-proposed tool. Created DISABLED unless explicitly enabled."""
    name = (body.name or "").strip()
    if not name:
        raise HTTPException(400, "name обязателен")
    if not isinstance(body.config_json, dict) or not body.config_json.get("function"):
        raise HTTPException(400, "config_json должен содержать 'function'")
    exists = (await db.execute(
        select(TenantTool).where(TenantTool.tenant_id == tenant_id, TenantTool.name == name)
    )).scalar_one_or_none()
    if exists:
        raise HTTPException(409, f"Инструмент '{name}' уже существует")

    tool = TenantTool(
        tenant_id=tenant_id, name=name, description=body.description,
        config_json=body.config_json, is_active=bool(body.is_active), tool_type="function",
    )
    db.add(tool)
    await db.flush()
    await db.refresh(tool)
    return {"id": str(tool.id), "name": tool.name, "is_active": tool.is_active}
