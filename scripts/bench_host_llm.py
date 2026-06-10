#!/usr/bin/env python3
"""
Level-1 benchmark for the host LLM (raw vLLM, no AI Platform pipeline).

Measures: TTFT (time-to-first-token), total latency, throughput (tokens/sec)
on a small bench of typical prompts of different sizes.

Each prompt is run N times (default 3), with one warm-up run discarded.
Reports p50 / p95 / mean across runs.

Output: markdown report.

Usage:
    python3 bench_host_llm.py --runs 3 --out report.md
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

import requests

VLLM_URL = "http://172.10.100.9:8000/v1"
MODEL = "qwen3-14b"


@dataclass
class Prompt:
    name: str
    system: str
    user: str
    max_tokens: int = 256
    # If True, this prompt is "thinking-disabled" (most production tool-routing
    # calls disable thinking via extra_body — without it Qwen3 spends 80%
    # of its output budget on a <think> block).
    no_thinking: bool = True


BENCH_PROMPTS: list[Prompt] = [
    Prompt(
        name="trivial-greeting",
        system="Ты — помощник саппорта ISP. Отвечай коротко.",
        user="Привет!",
        max_tokens=32,
    ),
    Prompt(
        name="trivial-math",
        system="Ты — калькулятор. Только число.",
        user="Сколько будет 2+2?",
        max_tokens=10,
    ),
    Prompt(
        name="medium-explain",
        system="Ты — техническая поддержка ISP. Объясняй кратко и по делу.",
        user="Что такое DHCP lease и зачем он нужен?",
        max_tokens=200,
    ),
    Prompt(
        name="medium-format",
        system="Возвращай результат markdown-таблицей с колонками: устройство, ip, статус.",
        user=(
            "Сформируй таблицу для трёх устройств:\n"
            "1. switch-1 (172.10.100.1) — up\n"
            "2. olt-csr (172.10.100.5) — up\n"
            "3. onu-3401 (172.10.100.205) — down"
        ),
        max_tokens=200,
    ),
    Prompt(
        name="long-context-summary",
        system="Ты анализируешь логи. Выдай 3-bullet summary.",
        user=(
            "Лог за час:\n" +
            "\n".join(
                f"2026-05-24 04:{i:02d}:00 INFO switch-{i % 5} port {i % 24} link {'up' if i % 3 else 'down'}"
                for i in range(60)
            ) +
            "\n\nКратко (3 буллета): что главное произошло?"
        ),
        max_tokens=200,
    ),
    Prompt(
        name="reasoning-multistep",
        system="Ты — диагностика сети. Думай по шагам.",
        user=(
            "У абонента +380501234567 интернет работает с перебоями. "
            "За сутки 4 раза падал сеанс. ONU на BDCOM OLT, порт 8/12. "
            "RX оптической мощности -28 dBm (норма -8..-25). "
            "Какие 3 возможные причины и план диагностики?"
        ),
        max_tokens=400,
    ),
    Prompt(
        name="tool-selection-hint",
        system=(
            "Ты выбираешь tool. Доступные tools:\n"
            "- search_clients(query): поиск клиента по имени/телефону/адресу\n"
            "- search_dev_by_mac(mac): поиск устройства по MAC\n"
            "- search_dhcp_lease(filters): поиск DHCP lease\n"
            "- switch_command(switch_id, command): выполнить команду на свиче\n"
            "- ping(ips): пинг IP-адресов\n"
            "\nВыведи только имя tool и его параметры в формате JSON."
        ),
        user="Найди клиента по телефону +380501234567",
        max_tokens=80,
    ),
    Prompt(
        name="sort-table",
        system="Сортируй переданные строки по полю port (asc) и выведи markdown-таблицу.",
        user=(
            "Записи:\n"
            "port=5 state=up\n"
            "port=12 state=down\n"
            "port=3 state=up\n"
            "port=23 state=up\n"
            "port=1 state=down\n"
            "port=8 state=up\n"
            "port=2 state=up\n"
            "port=15 state=up\n"
        ),
        max_tokens=300,
    ),
]


@dataclass
class RunResult:
    prompt_name: str
    ttft_s: float
    total_s: float
    prompt_tokens: int
    completion_tokens: int
    reasoning_tokens: int  # qwen3 sends reasoning separately
    finish_reason: str
    error: str | None = None


def run_one(prompt: Prompt) -> RunResult:
    """Single request with streaming to measure TTFT."""
    payload = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": prompt.system},
            {"role": "user", "content": prompt.user},
        ],
        "temperature": 0,
        "max_tokens": prompt.max_tokens,
        "stream": True,
    }
    if prompt.no_thinking:
        payload["chat_template_kwargs"] = {"enable_thinking": False}

    t0 = time.perf_counter()
    ttft = None
    completion_tokens = 0
    reasoning_tokens = 0
    prompt_tokens = 0
    finish_reason = ""
    err = None

    try:
        with requests.post(
            f"{VLLM_URL}/chat/completions",
            json=payload,
            timeout=180,
            stream=True,
        ) as r:
            r.raise_for_status()
            for line in r.iter_lines():
                if not line or not line.startswith(b"data: "):
                    continue
                chunk = line[6:].decode()
                if chunk == "[DONE]":
                    break
                try:
                    obj = json.loads(chunk)
                except Exception:
                    continue
                # vLLM streaming chunks
                if "choices" in obj and obj["choices"]:
                    choice = obj["choices"][0]
                    delta = choice.get("delta", {})
                    content = delta.get("content")
                    reasoning = delta.get("reasoning_content") or delta.get("reasoning")
                    if (content or reasoning) and ttft is None:
                        ttft = time.perf_counter() - t0
                    if content:
                        completion_tokens += 1
                    if reasoning:
                        reasoning_tokens += 1
                    fr = choice.get("finish_reason")
                    if fr:
                        finish_reason = fr
                # final usage info comes in the last chunk
                usage = obj.get("usage")
                if usage:
                    prompt_tokens = usage.get("prompt_tokens", prompt_tokens)
                    completion_tokens = usage.get("completion_tokens", completion_tokens)
    except Exception as e:
        err = repr(e)

    total = time.perf_counter() - t0
    return RunResult(
        prompt_name=prompt.name,
        ttft_s=ttft or total,
        total_s=total,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        reasoning_tokens=reasoning_tokens,
        finish_reason=finish_reason,
        error=err,
    )


def _percentile(data: list[float], p: float) -> float:
    if not data:
        return 0.0
    s = sorted(data)
    k = (len(s) - 1) * p
    f = int(k)
    c = min(f + 1, len(s) - 1)
    return s[f] + (s[c] - s[f]) * (k - f)


def summarize(results: list[RunResult]) -> dict:
    if not results:
        return {}
    ttfts = [r.ttft_s for r in results if r.error is None]
    totals = [r.total_s for r in results if r.error is None]
    tokens = [r.completion_tokens for r in results if r.error is None]
    durations = [r.total_s for r in results if r.error is None]
    throughput = [t / d for t, d in zip(tokens, durations) if d > 0 and t > 0]
    return {
        "runs": len(results),
        "errors": sum(1 for r in results if r.error is not None),
        "ttft_p50": _percentile(ttfts, 0.5),
        "ttft_p95": _percentile(ttfts, 0.95),
        "total_p50": _percentile(totals, 0.5),
        "total_p95": _percentile(totals, 0.95),
        "throughput_p50": _percentile(throughput, 0.5),
        "avg_prompt_tokens": statistics.mean(r.prompt_tokens for r in results if r.error is None) if ttfts else 0,
        "avg_completion_tokens": statistics.mean(r.completion_tokens for r in results if r.error is None) if ttfts else 0,
        "avg_reasoning_tokens": statistics.mean(r.reasoning_tokens for r in results if r.error is None) if ttfts else 0,
    }


def write_report(by_prompt: dict[str, dict], out_path: Path) -> None:
    lines: list[str] = []
    lines.append("# Host LLM Benchmark — Level 1 (raw vLLM)\n")
    lines.append(f"_Model: `{MODEL}` @ `{VLLM_URL}`_\n")
    lines.append(f"_Generated: {time.strftime('%Y-%m-%d %H:%M:%S')}_\n")
    lines.append("\n## Summary\n")
    lines.append("| Prompt | TTFT p50 | TTFT p95 | Total p50 | Total p95 | Tok/s p50 | Prompt tok | Comp tok | Reason tok |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|")
    for name, s in by_prompt.items():
        lines.append(
            f"| `{name}` "
            f"| {s['ttft_p50']:.2f}s "
            f"| {s['ttft_p95']:.2f}s "
            f"| {s['total_p50']:.2f}s "
            f"| {s['total_p95']:.2f}s "
            f"| {s['throughput_p50']:.1f} "
            f"| {s['avg_prompt_tokens']:.0f} "
            f"| {s['avg_completion_tokens']:.0f} "
            f"| {s['avg_reasoning_tokens']:.0f} |"
        )
    lines.append("\n")
    lines.append("**TTFT** — время до первого токена. Влияет на perceived responsiveness.")
    lines.append("\n**Total** — полное время до закрытия стрима.")
    lines.append("\n**Tok/s** — completion tokens / total time. Реальный throughput.")
    lines.append("\n**Reason tok** — токены в reasoning channel (Qwen3 thinking).")
    lines.append("\n\nAll runs with `temperature=0`, `enable_thinking=false`.\n")
    out_path.write_text("\n".join(lines))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", type=int, default=3, help="Measured runs per prompt")
    parser.add_argument("--warmup", type=int, default=1, help="Warm-up runs (discarded)")
    parser.add_argument("--out", type=Path, default=Path("bench_report.md"))
    args = parser.parse_args()

    by_prompt: dict[str, list[RunResult]] = {}
    for prompt in BENCH_PROMPTS:
        print(f"\n[bench] {prompt.name} (warmup={args.warmup}, runs={args.runs})", file=sys.stderr)
        results: list[RunResult] = []
        for w in range(args.warmup):
            r = run_one(prompt)
            print(f"  warmup #{w+1}: ttft={r.ttft_s:.2f}s total={r.total_s:.2f}s", file=sys.stderr)
        for n in range(args.runs):
            r = run_one(prompt)
            results.append(r)
            note = f" err={r.error}" if r.error else f" comp={r.completion_tokens} reason={r.reasoning_tokens}"
            print(f"  run #{n+1}: ttft={r.ttft_s:.2f}s total={r.total_s:.2f}s{note}", file=sys.stderr)
        by_prompt[prompt.name] = results

    summary_by_prompt = {name: summarize(rs) for name, rs in by_prompt.items()}
    write_report(summary_by_prompt, args.out)
    print(f"\nReport written to {args.out}", file=sys.stderr)

    # Aggregate
    all_ttft = [r.ttft_s for rs in by_prompt.values() for r in rs if r.error is None]
    all_total = [r.total_s for rs in by_prompt.values() for r in rs if r.error is None]
    print(f"\nOverall: TTFT median {_percentile(all_ttft, 0.5):.2f}s, "
          f"Total median {_percentile(all_total, 0.5):.2f}s, "
          f"errors: {sum(1 for rs in by_prompt.values() for r in rs if r.error is not None)}",
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
