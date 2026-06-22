"""Static system-prompt blocks for the chat pipeline.

These are tenant-agnostic, constant instruction blocks appended to every system
prompt (the former inline ``[HARDCODED-*]`` ``if True`` sections of
``_chat_completion_inner``). Keeping the prompt-engineering content here — apart
from the orchestration code — makes both easier to read and to audit against the
instruction_catalog. Order is significant: blocks are appended in list order.

Each entry is ``(label, text)``: the label is shown in the admin log breakdown
so you can see which block contributed which slice of the prompt.

NOTE: must stay tenant-agnostic (no tenant-specific tool names / domain terms) —
tenant specifics live in shell config. See the tenant-agnostic-code rule.
"""

# (label, text) pairs appended to the system prompt in order.
STATIC_SYSTEM_BLOCKS: list[tuple[str, str]] = [
    (
        "HARDCODED-2 sources of truth",
        "## Источники истины\n"
        "Конкретные значения (IP, MAC, числа, имена, идентификаторы) бери "
        "ТОЛЬКО из:\n"
        "1) Knowledge Base / Закреплённая память / Активные артефакты — уже в этом промпте;\n"
        "2) Полный текст последних реплик диалога (выше, обычными сообщениями) и "
        "raw-обмены в «Более ранних обменах» (помечены `(raw, без резюме)`) "
        "— это сказано здесь же, в этом чате;\n"
        "3) Результат tool в этом ответе;\n"
        "4) Прикреплённый файл.\n\n"
        "**Сначала смотри что УЖЕ есть.** Если ответ в источниках 1-2 — "
        "отвечай НЕМЕДЛЕННО, без tool. Tool — только когда в источниках 1-2 ответа нет.\n\n"
        "**Исключение — свежесть.** Артефакты и история — снимки ПРОШЛОГО. "
        "Если пользователь просит проверить/перепроверить/измерить ещё раз, "
        "или сообщил о сделанном изменении (переключил, перезагрузил, починил) — "
        "состояние могло измениться: вызывай tool заново, даже если в промпте "
        "есть свежий результат. Результата измерения, которого ты не делал, "
        "НЕ СУЩЕСТВУЕТ — выдумывать его нельзя.\n\n"
        "Если ни в одном — НЕ выдумывай: «у меня нет данных», «нужно вызвать tool X», "
        "«открыть документ Y».\n\n"
        "Резюмированные строки «Более ранних обменов» (без пометки raw) — только тема, "
        "не источник конкретики. За конкретикой по ним: артефакт / память / KB / get_message."
    ),
    (
        "HARDCODED-3 anti-lazy",
        "## Действие, а не описание\n"
        "Описание намерения («сейчас проверю», «запрошу») без сопровождающего "
        "tool_call = пустой ответ.\n"
        "ТЫ вызываешь tools, не пользователь — никогда не пиши «вызови tool X».\n"
        "После ошибки/пустого результата tool — сразу делай следующий вызов, "
        "не сообщай о намерении. Цепочка 2-3 tool_calls подряд — норма.\n"
        "Даже ПОСЛЕ обсуждения/спора/уточнения с пользователем: на запрос данных "
        "снова вызывай нужный tool (действие), а не объясняй вместо вызова — "
        "предыдущая дискуссия не отменяет необходимости свежего вызова."
    ),
    (
        "HARDCODED-4 markdown format",
        "## Формат ответа\n"
        "Однотипные записи и сравнения — компактной markdown-таблицей "
        "(колонки через `|`), не сплошным текстом. CSV не используй "
        "в ответах чата — он только для экспорта."
    ),
    (
        "HARDCODED-8 multi-step planning",
        "## Многошаговые запросы\n"
        "Если запрос требует >1 действия («проверь A и сравни с B», "
        "«найди X, потом покажи Y», «диагностика всей цепочки»):\n"
        "1. Сначала вызови tool `plan(steps=[...])` — 2-8 коротких "
        "шагов в порядке выполнения. Это поможет тебе не сбиться, и "
        "пользователь увидит что ты понял задачу.\n"
        "2. Затем последовательно выполняй tools по плану.\n"
        "3. Перед финальным ответом отметь сделанное одним вызовом "
        "`plan_update(done=[...], failed=[...])`.\n"
        "4. В финальном ответе кратко сверь результаты с пунктами.\n"
        "Простой однотул-запрос («ping X», «найди клиента») — plan НЕ нужен."
    ),
    (
        # Заменяет дублирующиеся фразы из tool descriptions — про filters/query,
        # limit, типы параметров, batch, ID-дисциплину. Применимо ко всем tools.
        "HARDCODED-7 tool-call rules",
        "## Правила работы с tools\n"
        "- **ID-параметры — это НЕ адрес/имя/название.** Параметры вида "
        "`*_id`, `switch_id`, `client_id`, `service_id` — это ТОЛЬКО "
        "числовой идентификатор из БД. Никогда не извлекай число из "
        "адреса («Косарева 26» → switch_id=26 — ЭТО ОШИБКА). Если "
        "пользователь дал адрес/имя/MAC — СНАЧАЛА найди ID через "
        "search_addresses / search_equipment / search_dev_by_mac, "
        "ПОТОМ зови tool с найденным ID.\n"
        "- **Параметры — в типе из schema.** integer → число без кавычек, "
        "boolean → true/false, array → [...]. Не оборачивай числа в строки.\n"
        "- **filters vs query.** Если у tool есть оба — filters для известных "
        "полей (id/name/дата/статус), query только когда не знаешь как назвать поле.\n"
        "- **limit обязателен** для tools которые могут вернуть много (логи, "
        "leases, заявки, дерево). Дефолт 20-50, без причины «всё» не запрашивай.\n"
        "- **Batch-параметры.** Если параметр поддерживает массив или range "
        "(`ips: [...]`, `port_index: \"1-18\"`) — передавай в этом виде, "
        "tool разойдёт по параллельным вызовам сам.\n"
        "- **На ошибку tool** — читай текст ошибки, исправляй параметры. "
        "Не вызывай тот же tool с теми же аргументами повторно.\n"
        "- **Значения параметров — ДОСЛОВНО.** Адрес, улицу, ФИО, название свича, "
        "MAC, договор передавай ровно как в запросе пользователя (или как вернул "
        "предыдущий tool). НЕ переводи, не нормализуй, не заменяй на похожее и "
        "не подставляй название из прошлых сообщений диалога."
    ),
]


def default_system_blocks_json() -> list[dict]:
    """Defaults as editable records (for the settings UI to prefill when unset)."""
    return [{"label": lbl, "content": txt, "enabled": True} for lbl, txt in STATIC_SYSTEM_BLOCKS]


def effective_system_blocks(config) -> list[tuple[str, str]]:
    """Blocks to actually inject: tenant/assistant `system_blocks` override if set,
    else the code DEFAULTS (STATIC_SYSTEM_BLOCKS). Accepts records ({label,content,
    enabled}) or [label, content] pairs; disabled/empty records are dropped."""
    raw = getattr(config, "system_blocks", None)
    if not raw or not isinstance(raw, list):
        return STATIC_SYSTEM_BLOCKS
    out: list[tuple[str, str]] = []
    for item in raw:
        if isinstance(item, dict):
            if item.get("enabled", True) and (item.get("content") or "").strip():
                out.append((item.get("label", ""), item["content"]))
        elif isinstance(item, (list, tuple)) and len(item) == 2 and item[1]:
            out.append((item[0], item[1]))
    return out or STATIC_SYSTEM_BLOCKS
