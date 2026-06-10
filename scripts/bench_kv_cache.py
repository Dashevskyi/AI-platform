#!/usr/bin/env python3
"""
KV-cache benchmark: date-at-start vs date-at-end prompt ordering.

Tests directly against vLLM (bypasses AI Platform pipeline) so we isolate
exactly the variable we care about: does moving the date block from position 2
to the end of the system prompt improve prefix-cache hit rates?

Methodology:
  For each ordering (OLD=date-at-start, NEW=date-at-end):
  1. Send a "warm-up" request to fill the cache with static content.
  2. Send N follow-up requests where ONLY the date changes (±1 min).
  3. Read vLLM prefix_cache metrics delta → compute hit-rate for each format.

  In the OLD format, a date change at position 2 invalidates the entire
  suffix → near-zero cache hits.
  In the NEW format, a date change at the end only invalidates a tiny tail →
  most static tokens are already cached → high hit rate.
"""

from __future__ import annotations

import json
import statistics
import time
from dataclasses import dataclass, field

import requests

VLLM_URL = "http://172.10.100.9:8000/v1"
METRICS_URL = "http://172.10.100.9:8000/metrics"
MODEL = "qwen3-14b"

# ── Realistic AI Platform prompt content ─────────────────────────────────────
# Approximates the actual static blocks in production (without tenant secrets).
# Lengths are representative of what the backend sends.

LANG_PIN = (
    "## Язык ответа\n"
    "Всегда отвечай на **русском** языке, даже если пользователь пишет "
    "по-украински или по-английски. Технические аббревиатуры (IP, MAC, VLAN, "
    "BGP, OSPF, HTTP) оставляй как есть без перевода."
)

SYSTEM_PROMPT = (
    "Ты — AI технический специалист интернет-провайдера IT-Invest (Айти-инвест). "
    "Отвечай на языке запроса пользователя; используй эмодзи и значки там, "
    "где это уместно. Табличные данные ВСЕГДА выводи в таблицы, если не просят об ином."
)

ONTOLOGY = (
    "## Топология сети IT-Invest\n"
    "Оборудование: коммутаторы Eltex (MES2300, MES3300), маршрутизаторы "
    "Mikrotik, OLT ZTE C300/C320, ONT. Протоколы: BGP (transit + IX), "
    "OSPF (core), MPLS (L2VPN, L3VPN), VLAN 802.1q/qinq.\n\n"
    "Адресация: IPv4 46.201.0.0/16, IPv6 2a01:230::/32. "
    "DNS: ns1.it-invest.ua, ns2.it-invest.ua.\n\n"
    "Мониторинг: Zabbix (host: mon.it-invest.ua), Grafana (dashboards: "
    "bandwidth, latency, BGP sessions, GPON OLT). RADIUS: FreeRADIUS + "
    "Postgresql. Биллинг: UTM5 (API: utm.it-invest.ua:9090).\n\n"
    "Уровни сети:\n"
    "- Core: Mikrotik CCR2004, BGP-пиринг AS29632, AS13249;\n"
    "- Distribution: Eltex MES3300 (кольцо MSTP);\n"
    "- Access: Eltex MES2300, MES2324 (L2, VLAN per customer);\n"
    "- GPON: ZTE C300/C320 (up to 128 ONT per PON port).\n\n"
    "Типичные задачи: поиск клиента, диагностика линка, трассировка BGP, "
    "мониторинг OLT/ONT, управление VLAN, анализ логов.\n\n"
    "При диагностике клиента — последовательность: UTM5 (статус, тариф) → "
    "RADIUS (сессия) → коммутатор (порт, VLAN, ошибки) → OLT (ONT level, "
    "расстояние, ошибки) → ping/traceroute."
)

RULES = (
    "Rules:\n"
    "- Не раскрывай детали внутренней топологии без необходимости.\n"
    "- Пароли и ключи шифрования — никогда в ответах.\n"
    "- При эскалации: Уровень 1 → Уровень 2 → NOC."
)

HC2_SOURCES = (
    "## Источники истины\n"
    "Конкретные значения (IP, MAC, числа, имена, идентификаторы) бери "
    "ТОЛЬКО из:\n"
    "1) Knowledge Base / Закреплённая память / Активные артефакты — уже в этом промпте;\n"
    "2) Raw-обмены в Recent conversation (помечены `(raw, ...)`) — это сказано здесь же;\n"
    "3) Результат tool в этом ответе;\n"
    "4) Прикреплённый файл.\n\n"
    "**Сначала смотри что УЖЕ есть.** Если ответ в источниках 1-2 — "
    "отвечай НЕМЕДЛЕННО, без tool. Tool — только когда в источниках 1-2 ответа нет.\n\n"
    "Если ни в одном — НЕ выдумывай: «у меня нет данных», «нужно вызвать tool X».\n\n"
    "Резюмированные строки Recent conversation — только тема, не источник конкретики."
)

HC3_ANTI_LAZY = (
    "## Действие, а не описание\n"
    "Описание намерения («сейчас проверю», «запрошу») без сопровождающего "
    "tool_call = пустой ответ.\n"
    "ТЫ вызываешь tools, не пользователь — никогда не пиши «вызови tool X».\n"
    "После ошибки/пустого результата tool — сразу делай следующий вызов, "
    "не сообщай о намерении. Цепочка 2-3 tool_calls подряд — норма."
)

HC4_FORMAT = (
    "## Формат ответа\n"
    "Однотипные записи и сравнения — компактной markdown-таблицей "
    "(колонки через `|`), не сплошным текстом."
)

HC7_TOOLS = (
    "## Правила работы с tools\n"
    "- **ID-параметры — это НЕ адрес/имя/название.** Параметры вида "
    "`*_id`, `switch_id`, `client_id`, `service_id` — это ТОЛЬКО "
    "числовой идентификатор из БД. Никогда не извлекай число из "
    "адреса («Косарева 26» → switch_id=26 — ЭТО ОШИБКА).\n"
    "- **Параметры — в типе из schema.** integer → число без кавычек.\n"
    "- **filters vs query.** Если у tool есть оба — filters для известных полей.\n"
    "- **limit обязателен** для tools которые могут вернуть много.\n"
    "- **Batch-параметры.** Если параметр поддерживает массив — передавай в этом виде.\n"
    "- **На ошибку tool** — читай текст ошибки, исправляй параметры."
)

HC8_PLAN = (
    "## Многошаговые запросы\n"
    "Если запрос требует >1 действия («проверь A и сравни с B»):\n"
    "1. Сначала вызови tool `plan(steps=[...])` — 2-8 коротких шагов.\n"
    "2. Затем последовательно выполняй tools по плану.\n"
    "3. В финальном ответе кратко сверь результаты с пунктами.\n"
    "Простой однотул-запрос («ping X», «найди клиента») — plan НЕ нужен."
)

# All static blocks, in the order they appear in the pipeline (after lang pin,
# and after the date in the OLD format)
STATIC_BLOCKS = "\n\n".join([
    SYSTEM_PROMPT, ONTOLOGY, RULES,
    HC2_SOURCES, HC3_ANTI_LAZY, HC4_FORMAT, HC7_TOOLS, HC8_PLAN,
])


def make_date_block(minute_offset: int = 0) -> str:
    """Simulate the HARDCODED-0 date/time block (changes every minute)."""
    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone.utc) + timedelta(minutes=minute_offset)
    return (
        f"## Текущая дата и время\n"
        f"Сейчас: **{now.strftime('%Y-%m-%d %H:%M')}** "
        f"(UTC).\n"
        f"Используй для арифметики дат («завтра», «через N дней», «в этом месяце»)."
    )


def build_prompt_old(minute_offset: int = 0) -> str:
    """OLD format: language_pin + DATE + static_blocks."""
    return "\n\n".join([
        LANG_PIN,
        make_date_block(minute_offset),   # ← dynamic at position 2
        STATIC_BLOCKS,
    ])


def build_prompt_new(minute_offset: int = 0) -> str:
    """NEW format: language_pin + static_blocks + DATE."""
    return "\n\n".join([
        LANG_PIN,
        STATIC_BLOCKS,                     # static first
        make_date_block(minute_offset),   # ← dynamic at the end
    ])


# ── vLLM metrics snapshot ────────────────────────────────────────────────────

def get_cache_metrics() -> dict:
    resp = requests.get(METRICS_URL, timeout=5)
    resp.raise_for_status()
    hits = queries = 0.0
    for line in resp.text.splitlines():
        if line.startswith("vllm:prefix_cache_hits_total{"):
            hits = float(line.split()[-1])
        elif line.startswith("vllm:prefix_cache_queries_total{"):
            queries = float(line.split()[-1])
    return {"hits": hits, "queries": queries}


# ── vLLM completion request ──────────────────────────────────────────────────

def chat_complete(system_content: str, user_msg: str, max_tokens: int = 20) -> float:
    """Send a chat completion request, return TTFT (time-to-first-token) in ms."""
    payload = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": system_content},
            {"role": "user", "content": user_msg},
        ],
        "max_tokens": max_tokens,
        "temperature": 0.0,
        "stream": True,
        "extra_body": {"chat_template_kwargs": {"enable_thinking": False}},
    }
    t0 = time.perf_counter()
    first_token_time: float | None = None
    with requests.post(
        f"{VLLM_URL}/chat/completions",
        json=payload,
        stream=True,
        timeout=60,
    ) as resp:
        resp.raise_for_status()
        for raw_line in resp.iter_lines():
            if not raw_line:
                continue
            line = raw_line.decode() if isinstance(raw_line, bytes) else raw_line
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                break
            try:
                chunk = json.loads(data)
                delta = chunk["choices"][0].get("delta", {})
                if delta.get("content"):
                    if first_token_time is None:
                        first_token_time = time.perf_counter()
                    break  # we only need TTFT
            except (json.JSONDecodeError, KeyError, IndexError):
                continue
    if first_token_time is None:
        first_token_time = time.perf_counter()
    return (first_token_time - t0) * 1000  # ms


# ── Benchmark runner ─────────────────────────────────────────────────────────

def run_scenario(name: str, build_fn, n_warm: int = 8) -> dict:
    """
    Warm scenario:
    1. Request with minute_offset=0 (fills cache with static prefix).
    2. n_warm requests with minute_offset=1..n_warm (only date changes).
    Measures: vLLM cache metrics delta + TTFT list.
    """
    print(f"\n{'='*60}")
    print(f"Scenario: {name}")
    print(f"{'='*60}")

    user_msg = "Привет! Какой сегодня день?"

    # ── 1. Cold warm-up (fills cache with static prefix) ──
    print("  [warm-up] cold request (offset=0)...", end="", flush=True)
    m0 = get_cache_metrics()
    ttft_warmup = chat_complete(build_fn(0), user_msg)
    print(f" {ttft_warmup:.0f} ms")

    # Small pause so vLLM flushes metrics
    time.sleep(1.0)

    # ── 2. Measure: requests where date changed (only date differs) ──
    print(f"  [measure] {n_warm} requests with changed date...", flush=True)
    m_before = get_cache_metrics()
    ttfts = []
    for i in range(1, n_warm + 1):
        ttft = chat_complete(build_fn(i), user_msg)
        ttfts.append(ttft)
        print(f"    request {i}/{n_warm}: TTFT={ttft:.0f} ms")
        time.sleep(0.3)

    time.sleep(1.0)
    m_after = get_cache_metrics()

    delta_hits = m_after["hits"] - m_before["hits"]
    delta_queries = m_after["queries"] - m_before["queries"]
    hit_rate = (delta_hits / delta_queries * 100) if delta_queries > 0 else 0.0

    print(f"\n  Cache hits:    {delta_hits:.0f} / {delta_queries:.0f} tokens")
    print(f"  Hit rate:      {hit_rate:.1f}%")
    print(f"  TTFT p50:      {statistics.median(ttfts):.0f} ms")
    print(f"  TTFT mean:     {statistics.mean(ttfts):.0f} ms")
    print(f"  TTFT min/max:  {min(ttfts):.0f} / {max(ttfts):.0f} ms")

    return {
        "name": name,
        "hit_rate_pct": hit_rate,
        "delta_hits": delta_hits,
        "delta_queries": delta_queries,
        "ttft_p50_ms": statistics.median(ttfts),
        "ttft_mean_ms": statistics.mean(ttfts),
        "ttft_min_ms": min(ttfts),
        "ttft_max_ms": max(ttfts),
    }


# ── Main ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("KV-Cache Benchmark: date-at-start vs date-at-end")
    print("=" * 60)

    # Show prompt sizes
    p_old = build_prompt_old(0)
    p_new = build_prompt_new(0)
    print(f"Prompt chars — OLD: {len(p_old)}  NEW: {len(p_new)}")

    # ── Estimate static prefix length (tokens) ──
    # The static prefix is everything BEFORE the date block.
    # OLD: only lang_pin (~60 tokens) is before the date.
    # NEW: lang_pin + all static blocks (~800 tokens) are before the date.
    print(f"Static-before-date chars — OLD: {len(LANG_PIN)}  "
          f"NEW: {len(LANG_PIN) + 2 + len(STATIC_BLOCKS)}")
    print()

    results = []

    # Test OLD format first (date at position 2)
    results.append(run_scenario("OLD (date at position 2)", build_prompt_old, n_warm=8))

    # Brief pause to avoid cross-contamination
    print("\n  [pause 3s before next scenario]")
    time.sleep(3)

    # Test NEW format (date at end)
    results.append(run_scenario("NEW (date at end)", build_prompt_new, n_warm=8))

    # ── Summary ──────────────────────────────────────────────────────────────
    print("\n")
    print("=" * 60)
    print("BENCHMARK SUMMARY")
    print("=" * 60)
    print(f"{'Scenario':<30} {'Hit Rate':>10} {'TTFT p50':>10} {'TTFT mean':>10}")
    print("-" * 60)
    for r in results:
        print(
            f"{r['name']:<30} {r['hit_rate_pct']:>9.1f}% "
            f"{r['ttft_p50_ms']:>9.0f}ms {r['ttft_mean_ms']:>9.0f}ms"
        )

    if len(results) == 2:
        old_r, new_r = results
        hit_gain = new_r["hit_rate_pct"] - old_r["hit_rate_pct"]
        ttft_gain = old_r["ttft_p50_ms"] - new_r["ttft_p50_ms"]
        print("-" * 60)
        print(f"{'Cache hit gain:':<30} {hit_gain:>+9.1f}%")
        print(f"{'TTFT improvement (p50):':<30} {ttft_gain:>+9.0f}ms")
        print()
        if hit_gain > 5:
            print("✅ NEW format shows meaningful cache improvement.")
        elif hit_gain > 0:
            print("⚠️  NEW format shows marginal improvement.")
        else:
            print("❌ No significant difference — investigate vLLM config.")
