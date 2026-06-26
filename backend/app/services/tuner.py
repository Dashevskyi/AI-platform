"""Auto-tuning diagnosis + apply for assistant tool routing.

Read-only by design: `diagnose()` asks a HEAVY model to explain a failing audit
case and propose a bounded config change. Nothing is written until
`apply_recommendation()` runs (triggered by an explicit admin "Apply").

Mutation levers (whitelist — the heavy model may propose ONLY these):
  description        → config_json.function.description                 (tool-wide)
  param_description  → config_json.function.parameters.properties.<p>.description
  arg_format         → config_json.x_backend_config.arg_formats[<path>] (deterministic)
  usage_example      → config_json.x_backend_config.usage_examples[]    (append)
  capability_tag     → config_json.x_backend_config.capability_tags[]   (append)
  tier0              → config_json.x_backend_config.tier0_template       (deterministic)
  ontology           → assistant.overrides.ontology_prompt              (assistant-scoped)
"""
from __future__ import annotations

import json
import logging

logger = logging.getLogger(__name__)

LEVER_WHITELIST = {
    "description", "param_description", "arg_format",
    "usage_example", "capability_tag", "tier0", "ontology",
}
DETERMINISTIC_LEVERS = {"arg_format", "tier0"}

_SYSTEM = """Ты — инженер по надёжности tool-calling. Лёгкая LLM в проде ошибается
на конкретном тест-кейсе: либо не вызывает нужный инструмент, либо вызывает не тот,
либо вызывает с неверными аргументами. Твоя задача — поставить диагноз и предложить
ТОЧЕЧНЫЕ правки КОНФИГА (не кода), чтобы лёгкая модель в следующий раз справилась.

ПРИНЦИП (важно): сначала ДЕТЕРМИНИРОВАННЫЕ рычаги, потом вероятностные.
- Если модель шлёт аргумент в неверном формате/регистре (напр. MAC через дефис, а
  нужно с двоеточиями; адрес в верхнем регистре) — НЕ проси «улучшить описание»,
  а предложи arg_format (серверная коэрция) — это надёжнее.
- Если модель не выбирает нужный тул на перефразировке — добавь usage_example с
  такой формулировкой и/или уточни description (чем этот тул отличается от похожих),
  либо tier0 (детерминированный роут по ключевым словам).

Доступные change_type (ТОЛЬКО из этого списка):
  arg_format       — коэрция аргумента. value = {"path": "<имя_параметра или путь>", "pipeline": "<операции>"}.
                     операции format_template: extract:hex | template:xx:xx:xx:xx:xx:xx | lower | upper | trim | re_sub:...
  param_description— уточнить описание параметра. value = {"param": "<имя>", "text": "<новое описание>"}.
  description      — уточнить описание тула (когда вызывать, отличие от похожих). value = "<новое описание>".
  usage_example    — добавить пример запроса. value = "<фраза пользователя>".
  capability_tag   — добавить тег. value = "<тег>".
  tier0            — детерминированный роут. value = {"keyword_regex": "...", "param_maps": [{"<param>": "$keyword_extract"}], "required_entity": "keyword_extract"}.
  ontology         — правка инструкции ассистента (когда какой тул). value = "<строка-инструкция для добавления>".

Верни СТРОГО JSON-массив (без префиксов/markdown). Каждый элемент:
{"change_type": "...", "value": <по схеме выше>, "rationale": "<кратко почему это поможет>"}
Если правка не нужна или причина вне конфига (напр. тул не предложен из-за скоупа) —
верни [] и опиши это в rationale одного элемента с change_type "ontology" ТОЛЬКО если уместно.
Минимум воды. 1–3 правки максимум, самые действенные."""


def build_user_prompt(case: dict, trace: dict, tool_cfg: dict | None, tool_name: str) -> str:
    fn = (tool_cfg or {}).get("function", {}) if isinstance(tool_cfg, dict) else {}
    xb = (tool_cfg or {}).get("x_backend_config", {}) if isinstance(tool_cfg, dict) else {}
    params = (fn.get("parameters") or {}).get("properties") or {}
    param_lines = "\n".join(
        f"  - {p}: {(d or {}).get('description', '') if isinstance(d, dict) else ''}"
        for p, d in params.items()
    ) or "  (нет параметров)"
    return f"""ТЕСТ-КЕЙС:
  Вопрос пользователя: {case.get('question')}
  Ожидался тул: {tool_name}
  Класс ошибки: {trace.get('failure_class')}

ЧТО СДЕЛАЛА ЛЁГКАЯ МОДЕЛЬ:
  Тулы, предложенные ей: {', '.join(trace.get('tools_offered') or []) or '—'}
  Тулы, которые она вызвала: {', '.join(trace.get('called') or []) or '(ни одного)'}
  Аргументы вызовов: {json.dumps(trace.get('call_args') or {}, ensure_ascii=False)[:800]}
  Результаты/ошибки тулов: {json.dumps(trace.get('tool_results') or {}, ensure_ascii=False)[:800]}
  Финальный ответ: {(trace.get('final_content') or '')[:300]}

ТЕКУЩИЙ КОНФИГ ТУЛА «{tool_name}»:
  description: {fn.get('description', '')}
  параметры:
{param_lines}
  usage_examples: {json.dumps(xb.get('usage_examples') or [], ensure_ascii=False)}
  arg_formats: {json.dumps(xb.get('arg_formats') or {}, ensure_ascii=False)}
  tier0: {'есть' if xb.get('tier0_template') else 'нет'}

Поставь диагноз и верни JSON-массив правок."""


def _extract_json_array(text: str) -> list:
    """Tolerant: pull proposals out of the model's reply. Reasoning models (V4)
    can TRUNCATE the JSON array (finish=length), so we don't require a closing
    `]` — instead we scan from the first `[` for balanced top-level `{...}`
    objects and json.loads each one, dropping any trailing incomplete object."""
    if not text:
        return []
    s = text.strip()
    if "```" in s:  # strip markdown fences
        s = s.replace("```json", "").replace("```", "")
    start = s.find("[")
    if start == -1:
        return []
    out: list = []
    i, n = start, len(s)
    while i < n:
        if s[i] == "{":
            depth, j, in_str, esc = 0, i, False, False
            while j < n:
                ch = s[j]
                if in_str:
                    if esc:
                        esc = False
                    elif ch == "\\":
                        esc = True
                    elif ch == '"':
                        in_str = False
                elif ch == '"':
                    in_str = True
                elif ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        try:
                            out.append(json.loads(s[i:j + 1]))
                        except json.JSONDecodeError:
                            pass
                        i = j
                        break
                j += 1
            else:
                break  # unbalanced (truncated) — stop
        i += 1
    return [o for o in out if isinstance(o, dict)]


async def diagnose(provider, model_name: str, case: dict, trace: dict,
                   tool_cfg: dict | None, tool_name: str) -> list[dict]:
    """Ask the heavy model for bounded config-change proposals. Returns a list of
    {change_type, value, rationale, deterministic} (filtered to the whitelist)."""
    try:
        resp = await provider.chat_completion(
            messages=[
                {"role": "system", "content": _SYSTEM},
                {"role": "user", "content": build_user_prompt(case, trace, tool_cfg, tool_name)},
            ],
            model=model_name,
            temperature=0.1,
            # V4-Flash reasons heavily (~2–3k reasoning tokens) before the JSON;
            # give enough headroom so the answer isn't truncated. The tolerant
            # parser recovers anyway if it still gets cut.
            max_tokens=8000,
        )
    except Exception as e:
        logger.warning("tuner diagnose LLM call failed: %s", str(e)[:200])
        return []
    proposals = _extract_json_array(resp.content or "")
    out = []
    for p in proposals:
        if not isinstance(p, dict):
            continue
        ct = str(p.get("change_type") or "").strip()
        if ct not in LEVER_WHITELIST:
            continue
        out.append({
            "change_type": ct,
            "value": p.get("value"),
            "rationale": str(p.get("rationale") or "")[:600],
            "deterministic": ct in DETERMINISTIC_LEVERS,
        })
    return out


# ----- apply (the ONLY write path; runs on explicit admin Apply) -----

def _ensure(d: dict, *keys) -> dict:
    cur = d
    for k in keys:
        if not isinstance(cur.get(k), dict):
            cur[k] = {}
        cur = cur[k]
    return cur


def apply_to_tool_config(cfg: dict, change_type: str, value, param_name: str | None) -> dict:
    """Return a NEW config_json with the change applied. Pure (caller persists)."""
    cfg = json.loads(json.dumps(cfg or {}))  # deep copy
    fn = _ensure(cfg, "function")
    xb = _ensure(cfg, "x_backend_config")
    if change_type == "description":
        fn["description"] = value if isinstance(value, str) else str(value)
    elif change_type == "param_description":
        param = (value or {}).get("param") if isinstance(value, dict) else param_name
        text = (value or {}).get("text") if isinstance(value, dict) else value
        props = _ensure(fn, "parameters", "properties")
        node = props.get(param) if isinstance(props.get(param), dict) else {}
        node["description"] = str(text)
        node.setdefault("type", "string")
        props[param] = node
    elif change_type == "arg_format":
        path = (value or {}).get("path") if isinstance(value, dict) else param_name
        pipeline = (value or {}).get("pipeline") if isinstance(value, dict) else value
        af = xb.get("arg_formats") if isinstance(xb.get("arg_formats"), dict) else {}
        af[str(path)] = str(pipeline)
        xb["arg_formats"] = af
    elif change_type == "usage_example":
        lst = xb.get("usage_examples") if isinstance(xb.get("usage_examples"), list) else []
        if value not in lst:
            lst.append(value if isinstance(value, str) else str(value))
        xb["usage_examples"] = lst
    elif change_type == "capability_tag":
        lst = xb.get("capability_tags") if isinstance(xb.get("capability_tags"), list) else []
        if value not in lst:
            lst.append(value if isinstance(value, str) else str(value))
        xb["capability_tags"] = lst
    elif change_type == "tier0":
        xb["tier0_template"] = value
    return cfg
