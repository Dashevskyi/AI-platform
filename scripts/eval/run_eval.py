#!/usr/bin/env python3
"""
AI Platform eval harness — regression net for prompt / model / pipeline changes.

Sends a set of golden questions through the real tenant API (full pipeline +
real LLM), then inspects the resulting llm_request_log row to assert behavior:
which tier answered, whether a tool was called, the answer language, etc.

Run it against a configured tenant (e.g. IT-Invest) so the golden cases can use
that tenant's real tools and KB. Use it before changing a model, the system
prompt, or refactoring the pipeline — a green run means behavior held.

Usage (on the server, from repo root):
    PYTHONPATH=backend backend/venv/bin/python3 scripts/eval/run_eval.py \
        --base-url http://127.0.0.1:8000 \
        --tenant <TENANT_ID> --api-key <RAW_API_KEY> \
        --cases scripts/eval/cases.example.yaml \
        --out scripts/eval/report.md

Exit code is non-zero if any case fails (CI-friendly).

Supported assertions per case (all optional):
    content_contains:      [str, ...]   every substring must appear (case-insensitive)
    content_not_contains:  [str, ...]   none may appear
    content_regex:         str          must match (re.search)
    lang:                  ru|uk|en     detected language of the answer
    served_by:             tier0_template|llm
    tool_called:           true|false   any tool invoked this turn
    min_tool_calls:        int
    max_tool_calls:        int
    expect_tool:           str          tool name appears in the request log
    max_latency_ms:        number
"""
from __future__ import annotations

import argparse
import asyncio
import re
import sys
import uuid
from pathlib import Path

import httpx
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND = REPO_ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.core.database import async_session  # noqa: E402
from app.models.llm_request_log import LLMRequestLog  # noqa: E402
from app.services.llm.pipeline import _detect_user_language  # noqa: E402
from sqlalchemy import select  # noqa: E402


def _detect_lang(text: str) -> str:
    # _detect_user_language returns 'русском' / 'украинском' / None (it's worded
    # for a "respond in <lang>" hint). None means it saw no Cyrillic majority.
    lang = _detect_user_language(text or "")
    if lang == "русском":
        return "ru"
    if lang == "украинском":
        return "uk"
    return "en"


async def _latest_log(chat_id: str) -> LLMRequestLog | None:
    async with async_session() as db:
        row = (await db.execute(
            select(LLMRequestLog)
            .where(LLMRequestLog.chat_id == uuid.UUID(chat_id))
            .order_by(LLMRequestLog.created_at.desc())
            .limit(1)
        )).scalars().first()
        return row


def _log_mentions_tool(log: LLMRequestLog, tool_name: str) -> bool:
    """Loose check: does the tool name appear anywhere in the request/debug?"""
    import json
    blobs = []
    for attr in ("raw_request", "normalized_request", "debug", "normalized_response"):
        val = getattr(log, attr, None)
        if val:
            try:
                blobs.append(json.dumps(val, ensure_ascii=False))
            except Exception:
                pass
    haystack = " ".join(blobs)
    return tool_name in haystack


def _evaluate(case: dict, content: str, log: LLMRequestLog | None) -> list[str]:
    """Return a list of failure messages (empty = passed)."""
    a = case.get("assert", {}) or {}
    fails: list[str] = []
    low = (content or "").lower()

    for sub in a.get("content_contains", []) or []:
        if sub.lower() not in low:
            fails.append(f"content_contains: missing {sub!r}")
    for sub in a.get("content_not_contains", []) or []:
        if sub.lower() in low:
            fails.append(f"content_not_contains: found {sub!r}")
    if a.get("content_regex"):
        if not re.search(a["content_regex"], content or "", re.IGNORECASE):
            fails.append(f"content_regex: no match for {a['content_regex']!r}")
    if a.get("lang"):
        got = _detect_lang(content)
        if got != a["lang"]:
            fails.append(f"lang: expected {a['lang']}, got {got}")

    # Log-derived assertions
    if any(k in a for k in ("served_by", "tool_called", "min_tool_calls", "max_tool_calls", "expect_tool", "max_latency_ms")):
        if log is None:
            fails.append("no request log row found for chat (cannot check log-based asserts)")
            return fails
        if a.get("served_by") and (log.served_by or "llm") != a["served_by"]:
            fails.append(f"served_by: expected {a['served_by']}, got {log.served_by or 'llm'}")
        tc = log.tool_calls_count or 0
        if "tool_called" in a and bool(tc > 0) != bool(a["tool_called"]):
            fails.append(f"tool_called: expected {a['tool_called']}, got {tc > 0} ({tc} calls)")
        if "min_tool_calls" in a and tc < a["min_tool_calls"]:
            fails.append(f"min_tool_calls: expected ≥{a['min_tool_calls']}, got {tc}")
        if "max_tool_calls" in a and tc > a["max_tool_calls"]:
            fails.append(f"max_tool_calls: expected ≤{a['max_tool_calls']}, got {tc}")
        if a.get("expect_tool") and not _log_mentions_tool(log, a["expect_tool"]):
            fails.append(f"expect_tool: {a['expect_tool']!r} not found in request log")
        if a.get("max_latency_ms") and (log.latency_ms or 0) > a["max_latency_ms"]:
            fails.append(f"max_latency_ms: expected ≤{a['max_latency_ms']}, got {log.latency_ms}")
    return fails


async def run(args) -> int:
    cases = yaml.safe_load(Path(args.cases).read_text(encoding="utf-8")) or []
    if not isinstance(cases, list):
        print("cases file must be a YAML list", file=sys.stderr)
        return 2

    headers = {"X-API-Key": args.api_key}
    results = []
    tier0_hits = 0

    async with httpx.AsyncClient(base_url=args.base_url, headers=headers, timeout=args.timeout) as c:
        for case in cases:
            name = case.get("name", case.get("question", "")[:50])
            question = case["question"]
            # Fresh chat per case for isolation.
            r = await c.post(f"/api/tenants/{args.tenant}/chats/", json={"title": f"eval: {name}"})
            r.raise_for_status()
            chat_id = r.json()["id"]
            try:
                r = await c.post(
                    f"/api/tenants/{args.tenant}/chats/{chat_id}/messages",
                    json={"content": question, "idempotency_key": str(uuid.uuid4())},
                )
                r.raise_for_status()
                content = r.json().get("content", "")
            except Exception as exc:
                results.append((name, ["request failed: " + str(exc)[:200]], ""))
                continue

            log = await _latest_log(chat_id)
            if log and (log.served_by == "tier0_template"):
                tier0_hits += 1
            fails = _evaluate(case, content, log)
            results.append((name, fails, content))

    # Report
    passed = sum(1 for _, f, _ in results if not f)
    total = len(results)
    lines = [
        f"# Eval report — {passed}/{total} passed",
        f"Tier 0 served: {tier0_hits}/{total} ({(tier0_hits/total*100 if total else 0):.0f}%)",
        "",
    ]
    for name, fails, content in results:
        mark = "✅" if not fails else "❌"
        lines.append(f"## {mark} {name}")
        if fails:
            for f in fails:
                lines.append(f"  - FAIL: {f}")
        lines.append(f"  answer: {(content or '')[:300]!r}")
        lines.append("")

    report = "\n".join(lines)
    print(report)
    if args.out:
        Path(args.out).write_text(report, encoding="utf-8")
        print(f"\n[report written to {args.out}]")
    return 0 if passed == total else 1


def main():
    ap = argparse.ArgumentParser(description="AI Platform eval harness")
    ap.add_argument("--base-url", default="http://127.0.0.1:8000")
    ap.add_argument("--tenant", required=True, help="tenant UUID")
    ap.add_argument("--api-key", required=True, help="raw tenant API key")
    ap.add_argument("--cases", default=str(Path(__file__).parent / "cases.example.yaml"))
    ap.add_argument("--out", default=None, help="optional path for a markdown report")
    ap.add_argument("--timeout", type=float, default=180.0)
    args = ap.parse_args()
    raise SystemExit(asyncio.run(run(args)))


if __name__ == "__main__":
    main()
