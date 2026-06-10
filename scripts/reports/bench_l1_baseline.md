# Host LLM Benchmark — Level 1 (raw vLLM)

_Model: `qwen3-14b` @ `http://172.10.100.9:8000/v1`_

_Generated: 2026-05-24 06:02:36_


## Summary

| Prompt | TTFT p50 | TTFT p95 | Total p50 | Total p95 | Tok/s p50 | Prompt tok | Comp tok | Reason tok |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| `trivial-greeting` | 0.07s | 0.07s | 0.45s | 0.45s | 24.5 | 0 | 11 | 0 |
| `trivial-math` | 0.07s | 0.07s | 0.11s | 0.11s | 9.3 | 0 | 1 | 0 |
| `medium-explain` | 0.07s | 0.07s | 6.77s | 6.77s | 28.8 | 0 | 195 | 0 |
| `medium-format` | 0.07s | 0.07s | 4.03s | 4.03s | 28.6 | 0 | 115 | 0 |
| `long-context-summary` | 0.07s | 0.07s | 4.56s | 4.57s | 27.8 | 0 | 127 | 0 |
| `reasoning-multistep` | 0.05s | 0.05s | 13.84s | 13.84s | 28.9 | 0 | 400 | 0 |
| `tool-selection-hint` | 0.07s | 0.07s | 1.28s | 1.28s | 27.4 | 0 | 35 | 0 |
| `sort-table` | 0.07s | 0.07s | 3.79s | 3.79s | 28.5 | 0 | 108 | 0 |


**TTFT** — время до первого токена. Влияет на perceived responsiveness.

**Total** — полное время до закрытия стрима.

**Tok/s** — completion tokens / total time. Реальный throughput.

**Reason tok** — токены в reasoning channel (Qwen3 thinking).


All runs with `temperature=0`, `enable_thinking=false`.
