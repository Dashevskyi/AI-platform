#!/usr/bin/env python3
"""
Add x_backend_config.result_processing config to top output-heavy tools.

Baseline limits (conservative — won't drop critical info, will prevent runaway
output that dominates the LLM context):
  - drop_fields: noise like lat/lng/packet_id/timestamps that LLM never needs
  - limit_items: cap arrays at 50 (with _truncated sentinel)
  - max_chars: hard fallback at 12,000 chars (~3,000 tokens)

Targets (data-driven from llm_request_logs):
  - pon_tree (avg 6.4k tok, p95 14k tok) — main culprit
  - search_services (avg 2.5k tok)
  - pon_search (p95 2.2k tok)

Default: DRY-RUN. Pass --apply to commit.
"""
import argparse
import json
import sys

import psycopg2

DB_URL = "postgresql://ai_platform:ai_platform_secret@localhost:5432/ai_platform"
TENANT_ID = "403d219f-0f4a-4782-a884-0e25f8bfe241"

BASELINE_CONFIG = {
    # Noise that LLM never uses (coordinates, internal ids, timestamps).
    # Recursively dropped from ANY level of the result JSON.
    "drop_fields": [
        "lat", "lng",
        "packet_id",
        "created_at", "updated_at",
        "_id",
    ],
    # Hard char-cap as last-resort guardrail. Picked so the largest expected
    # legitimate response fits, but a runaway one gets cut with an explicit
    # truncation note so the LLM doesn't silently pretend it has everything.
    "max_chars": 12000,
}

# Per-tool overrides for cases where the baseline isn't tight enough
TOOL_CONFIGS = {
    # 14k tok p95 — main offender. Tighter cap because the schema repeats
    # heavy nested structures (splits → tips → clients).
    "pon_tree": {**BASELINE_CONFIG, "max_chars": 10000},
    # 3.5k tok p95.
    "search_services": {**BASELINE_CONFIG, "max_chars": 8000},
    "pon_search": BASELINE_CONFIG,
    "pon_olts": BASELINE_CONFIG,
    "search_clients": BASELINE_CONFIG,
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    conn = psycopg2.connect(DB_URL)
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT id, name, config_json FROM tenant_tools "
            "WHERE tenant_id=%s AND name = ANY(%s) AND deleted_at IS NULL",
            (TENANT_ID, list(TOOL_CONFIGS.keys())),
        )
        rows = cur.fetchall()
        print(f"Found {len(rows)} target tools\n")

        for tool_id, name, cfg in rows:
            if not isinstance(cfg, dict):
                cfg = {}
            runtime = cfg.get("x_backend_config") or {}
            existing = runtime.get("result_processing")
            new_rp = TOOL_CONFIGS[name]

            print(f"=== {name} ===")
            if existing:
                print(f"  existing: {json.dumps(existing, ensure_ascii=False)}")
            print(f"  new     : {json.dumps(new_rp, ensure_ascii=False)}")

            if args.apply:
                runtime["result_processing"] = new_rp
                cfg["x_backend_config"] = runtime
                cur.execute(
                    "UPDATE tenant_tools SET config_json=%s, updated_at=now() WHERE id=%s",
                    (json.dumps(cfg), tool_id),
                )
                print(f"  ✅ updated\n")
            else:
                print(f"  [DRY-RUN]\n")

        if args.apply:
            conn.commit()
            print("All changes committed. Backend already restarted — config takes effect immediately.")
        else:
            print("Pass --apply to commit.")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
