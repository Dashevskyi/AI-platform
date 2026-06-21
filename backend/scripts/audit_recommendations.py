"""Tool-config audit: turn eval failures into a PRIORITIZED recommendation summary.

The engine behind the planned audit UI. Reads a model's corpus failures, groups
tool-selection misses by the INTENDED tool, computes each group's share of all
failures ("fix this → ~N% of misses"), and asks the heavy model to produce a
concrete fix (rewrite description / add tags / add example / disambiguate vs the
wrongly-called tool) + one-line reasoning. Output is ranked by impact.

Run: PYTHONPATH=. venv/bin/python scripts/audit_recommendations.py /tmp/corpus_fails_Qwen3-30B-2507.tsv
"""
import asyncio
import csv
import json
import sys
from collections import Counter, defaultdict

import httpx
from sqlalchemy import text

from app.core.database import engine
from app.core.security import decrypt_value

TENANT = "403d219f-0f4a-4782-a884-0e25f8bfe241"
TOP_GROUPS = 8


async def _heavy_key(c):
    enc = (await c.execute(text("SELECT api_key_enc FROM llm_models WHERE name='DeepSeek Chat'"))).scalar()
    return decrypt_value(enc) if enc else None


async def _tool_desc(c, name):
    cfg = (await c.execute(text(
        "SELECT config_json FROM tenant_tools WHERE tenant_id=:t AND name=:n"), {"t": TENANT, "n": name})).scalar() or {}
    fn = (cfg.get("function") or {})
    tags = ((cfg.get("x_backend_config") or {}).get("capability_tags")) or []
    return (fn.get("description") or ""), tags


async def recommend(key, tool, desc, tags, samples, called_dist):
    prompt = (
        "Ты — аудитор конфигурации tools для LLM-ассистента интернет-провайдера. "
        f"Инструмент `{tool}` ДОЛЖЕН был вызываться, но модель его не выбрала.\n"
        f"Текущее описание: {desc!r}\n"
        f"Текущие capability_tags: {tags}\n"
        f"Примеры запросов, где он НЕ был выбран: {samples}\n"
        f"Что модель звала вместо него: {dict(called_dist)}\n\n"
        "Дай КОНКРЕТНУЮ правку, чтобы pipeline выбирал этот tool: "
        "1) переписанное описание (1-2 предложения, чётко «когда использовать», и чем отличается от того, что звали вместо); "
        "2) какие capability_tags добавить; "
        "3) одно предложение reasoning почему промахивались. "
        'Верни СТРОГО JSON: {"new_description": "...", "add_tags": ["..."], "reason": "..."}'
    )
    try:
        async with httpx.AsyncClient(timeout=60) as cl:
            r = await cl.post("https://api.deepseek.com/chat/completions",
                              headers={"Authorization": f"Bearer {key}"},
                              json={"model": "deepseek-chat", "messages": [{"role": "user", "content": prompt}],
                                    "max_tokens": 400, "temperature": 0, "response_format": {"type": "json_object"}})
        raw = r.json()["choices"][0]["message"]["content"]
        return json.loads(raw[raw.index("{"):])
    except Exception as e:
        return {"error": str(e)[:80]}


async def main():
    path = sys.argv[1] if len(sys.argv) > 1 else "/tmp/corpus_fails_Qwen3-30B-2507.tsv"
    rows = list(csv.DictReader(open(path, encoding="utf-8"), delimiter="\t"))
    # tool-selection misses only (expected a tool, 'tool' among fails)
    misses = [r for r in rows if "tool" in (r["fails"] or "") and r["expected"] not in ("NO_TOOL", "?")]
    by_tool = defaultdict(list)
    for r in misses:
        by_tool[r["expected"]].append(r)
    total = len(misses)
    print(f"\n=== AUDIT: {path.split('/')[-1]} ===")
    print(f"tool-selection промахов: {total} (из них группируем по ожидаемому тулу)\n")

    ranked = sorted(by_tool.items(), key=lambda kv: -len(kv[1]))[:TOP_GROUPS]
    async with engine.connect() as c:
        key = await _heavy_key(c)
        for tool, items in ranked:
            share = 100 * len(items) // max(total, 1)
            called = Counter(x["called"] or "(ничего)" for x in items)
            samples = [x["question"][:60] for x in items[:5]]
            desc, tags = await _tool_desc(c, tool)
            rec = await recommend(key, tool, desc, tags, samples, called.most_common(3)) if key else {}
            print(f"▸ ~{share}% ({len(items)} кейсов) — `{tool}`")
            print(f"    звали вместо: {dict(called.most_common(3))}")
            if rec.get("new_description"):
                print(f"    FIX описание: {rec['new_description']}")
                print(f"    +теги: {rec.get('add_tags')}")
                print(f"    почему: {rec.get('reason')}")
            elif rec.get("error"):
                print(f"    [agent error: {rec['error']}]")
            print()
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
