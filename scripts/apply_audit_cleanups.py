#!/usr/bin/env python3
"""
Apply cleanups identified by audit_instructions.py.

Cleanups:
  1. tenant_tools.description that duplicates config_json.function.description
     → set top-level description = NULL (pipeline reads function.description anyway)
  2. tenant_shell_configs.system_prompt that matches the default placeholder
     "You are a helpful assistant. Reply briefly." → set NULL

Default behaviour is DRY-RUN: shows what WOULD change, exits without writing.
Pass --apply to actually update the DB.

Usage:
    python3 apply_audit_cleanups.py           # dry run
    python3 apply_audit_cleanups.py --apply   # actually update
"""
from __future__ import annotations

import argparse
import sys

import numpy as np
import psycopg2
import requests

DB_URL = "postgresql://ai_platform:ai_platform_secret@localhost:5432/ai_platform"
OLLAMA_URL = "http://localhost:11434"
EMBED_MODEL = "bge-m3:latest"

DEFAULT_PLACEHOLDER_PROMPTS = {
    "You are a helpful assistant. Reply briefly.",
    "You are a helpful assistant.",
}

# Cosine threshold above which we consider top-level description and
# function.description "the same enough" to drop the top-level.
DESC_DUP_THRESHOLD = 0.95


def embed(text: str) -> np.ndarray:
    r = requests.post(
        f"{OLLAMA_URL}/api/embeddings",
        json={"model": EMBED_MODEL, "prompt": text},
        timeout=60,
    )
    r.raise_for_status()
    v = np.array(r.json()["embedding"], dtype=np.float32)
    n = np.linalg.norm(v)
    if n > 0:
        v /= n
    return v


def cosine(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.dot(a, b))


def find_dup_tool_descriptions(conn) -> list[tuple]:
    """Return rows (id, tenant_id, name, top_desc, fn_desc, similarity)."""
    cur = conn.cursor()
    cur.execute("""
        SELECT id, tenant_id, name, description, config_json
        FROM tenant_tools
        WHERE deleted_at IS NULL
          AND description IS NOT NULL
          AND length(trim(description)) > 0
    """)
    candidates: list[tuple] = []
    for tool_id, tenant_id, name, top_desc, cfg in cur.fetchall():
        fn = (cfg or {}).get("function") or {}
        fn_desc = (fn.get("description") or "").strip()
        top_desc_s = (top_desc or "").strip()
        if not fn_desc or not top_desc_s:
            continue
        if top_desc_s == fn_desc:
            # exact match — no need to embed
            candidates.append((tool_id, tenant_id, name, top_desc_s, fn_desc, 1.0))
            continue
        # else compare semantically
        try:
            sim = cosine(embed(top_desc_s), embed(fn_desc))
        except Exception as e:
            print(f"  warn: embed failed for {name}: {e}", file=sys.stderr)
            continue
        if sim >= DESC_DUP_THRESHOLD:
            candidates.append((tool_id, tenant_id, name, top_desc_s, fn_desc, sim))
    return candidates


def find_placeholder_system_prompts(conn) -> list[tuple]:
    """Return rows (tenant_id, system_prompt) where prompt is a known placeholder."""
    cur = conn.cursor()
    cur.execute("""
        SELECT tenant_id, system_prompt
        FROM tenant_shell_configs
        WHERE system_prompt IS NOT NULL
    """)
    out: list[tuple] = []
    for tenant_id, sp in cur.fetchall():
        if sp and sp.strip() in DEFAULT_PLACEHOLDER_PROMPTS:
            out.append((tenant_id, sp.strip()))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="Actually write to DB (default: dry-run)")
    args = ap.parse_args()

    conn = psycopg2.connect(DB_URL)
    try:
        print("=" * 72)
        print("Cleanup 1: tenant_tools.description duplicating function.description")
        print("=" * 72)
        dup_descs = find_dup_tool_descriptions(conn)
        if not dup_descs:
            print("  Nothing to clean.")
        else:
            print(f"  Found {len(dup_descs)} tools where top-level description "
                  f"duplicates function.description:")
            for tool_id, tenant_id, name, top, fn, sim in dup_descs:
                tag = "EXACT" if sim >= 0.999 else f"sim={sim:.3f}"
                print(f"    - [{tag}] {name}  (tenant {str(tenant_id)[:8]}…)")
            if args.apply:
                cur = conn.cursor()
                cur.execute(
                    "UPDATE tenant_tools SET description = NULL WHERE id = ANY(%s::uuid[])",
                    ([str(r[0]) for r in dup_descs],),
                )
                print(f"  ✅ Updated {cur.rowcount} rows (description → NULL)")
            else:
                print("  [DRY-RUN] No DB changes. Pass --apply to update.")

        print()
        print("=" * 72)
        print("Cleanup 2: tenant_shell_configs.system_prompt placeholder defaults")
        print("=" * 72)
        placeholders = find_placeholder_system_prompts(conn)
        if not placeholders:
            print("  Nothing to clean.")
        else:
            print(f"  Found {len(placeholders)} tenants with placeholder system_prompt:")
            sample_text = placeholders[0][1] if placeholders else ""
            print(f"  Sample text: {sample_text!r}")
            if args.apply:
                cur = conn.cursor()
                cur.execute(
                    "UPDATE tenant_shell_configs SET system_prompt = NULL "
                    "WHERE tenant_id = ANY(%s::uuid[])",
                    ([str(p[0]) for p in placeholders],),
                )
                print(f"  ✅ Updated {cur.rowcount} rows (system_prompt → NULL)")
            else:
                print("  [DRY-RUN] No DB changes. Pass --apply to update.")

        if args.apply:
            conn.commit()
            print()
            print("All changes committed.")
        else:
            print()
            print("Nothing applied. Re-run with --apply to commit changes.")

    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
