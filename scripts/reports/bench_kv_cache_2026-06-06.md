# KV-Cache Benchmark: Date Position Optimization
**Date:** 2026-06-06  
**Model:** qwen3-14b (vLLM, http://172.10.100.9:8000)  
**Method:** Direct vLLM API, 8 warm requests per scenario (only date block changes)

## Setup

Prompt structure:
- **OLD**: `[lang_pin]` → **[date/time]** → `[system_prompt]` → `[ontology]` → `[rules]` → `[HC-2..8]`
- **NEW**: `[lang_pin]` → `[system_prompt]` → `[ontology]` → `[rules]` → `[HC-2..8]` → **[date/time]**

Prompt total: **3752 chars** (identical in both cases — only block order changes).  
Static-before-date:
- OLD: **201 chars** (only lang_pin before date)
- NEW: **3617 chars** (all static content before date)

## Results

| Scenario | Cache Hits | Hit Rate | TTFT p50 | TTFT mean |
|---|---|---|---|---|
| OLD (date at position 2) | 880 / 11696 tokens | **7.5%** | 1479 ms | 1477 ms |
| NEW (date at end) | 11248 / 11696 tokens | **96.2%** | 722 ms | 725 ms |

**Cache hit gain: +88.6 percentage points**  
**TTFT improvement (p50): −757 ms (2.05× faster)**

## Interpretation

With the **OLD** format, every time the date string changes (i.e., every minute), vLLM
cannot reuse any cached KV blocks for the system prompt — because the date is at token
position ~60 (right after the language pin), and any change at position N invalidates
all KV blocks from N onward. Result: only lang_pin (~880 tokens) is ever cached.

With the **NEW** format, the entire static system prompt (~3500 tokens) sits before the
date. When the date changes, vLLM reuses all those blocks. Only the date block (~200
tokens) + user message needs fresh computation. Result: 96.2% of prefill is cached,
cutting TTFT from ~1480ms to ~720ms.

## Applied Changes

File: `app/services/llm/pipeline.py`

1. **Line ~800**: Removed `_sys()` call for date. Instead stores text in `_hc0_date_text`.
2. **Line ~1217**: Added `_sys("HARDCODED-0 ...", _hc0_date_text)` BEFORE `messages.append()`.
3. **Line ~1220**: `messages: list[dict] = []` + system message assembly now AFTER date injection.

Bug fixed in the same pass: original injection attempt had `messages.append()` **before**
`_sys(date)`, meaning date was never in the prompt at all. The fix ensures correct ordering.
