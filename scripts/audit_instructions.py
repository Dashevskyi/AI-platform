#!/usr/bin/env python3
"""
Audit LLM-facing instruction texts across the platform.

Sources (8 places where text gets injected into the model context):
  1. HARDCODED-* blocks in pipeline.py (via _sys(label, content) calls)
  2. Built-in tools (BUILTIN_TOOLS list in builtin_registry.py)
     - description + each parameter description
  3. tenant_tools in DB
     - description, config_json.function.description
     - per-parameter descriptions in config_json.function.parameters.properties
  4. builtin_tool_overrides in DB (per-tenant description overrides)
  5. tenants.ontology_prompt (in tenant_shell_configs.ontology_prompt)
  6. tenant_shell_configs.system_prompt
  7. tenant_shell_configs.rules_text
  8. tenant_api_keys.memory_prompt + tenant_api_key_groups.memory_prompt

Output: markdown report listing pairs of instruction texts with cosine
similarity above a threshold. Each pair shows where the duplicates live, so
you can decide which one to keep / consolidate / delete.

Uses bge-m3 via Ollama (same model used for tool embeddings in the platform,
so similarity scores are consistent with the runtime ranker).

Usage:
    python3 audit_instructions.py [--threshold 0.85] [--out report.md]
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import psycopg2
import requests

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

REPO_ROOT = Path("/home/ai-platform")
PIPELINE_PY = REPO_ROOT / "backend/app/services/llm/pipeline.py"
BUILTIN_REGISTRY_PY = REPO_ROOT / "backend/app/services/tools/builtin_registry.py"

DB_URL = "postgresql://ai_platform:ai_platform_secret@localhost:5432/ai_platform"
OLLAMA_URL = "http://localhost:11434"
EMBED_MODEL = "bge-m3:latest"

# Minimum text length to bother embedding — tiny strings like "id" produce
# garbage cosine. Below this, we skip the item entirely.
MIN_TEXT_LEN = 30


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class Snippet:
    kind: str          # 'HC', 'builtin_desc', 'builtin_param', 'tool_desc', 'tool_param',
                       # 'ontology', 'system_prompt', 'rules_text', 'memory_prompt'
    source: str        # human-readable origin, e.g. "HARDCODED-7 tool-call rules"
    text: str          # the actual instruction text
    extra: dict = field(default_factory=dict)  # optional metadata (tool name, tenant_id, etc.)
    embedding: np.ndarray | None = None

    def short(self, n: int = 80) -> str:
        t = " ".join(self.text.split())
        return (t[:n] + "…") if len(t) > n else t


# ---------------------------------------------------------------------------
# Source extractors
# ---------------------------------------------------------------------------

def extract_hardcoded_blocks(path: Path) -> list[Snippet]:
    """Find _sys(label, content) calls in pipeline.py via AST.

    Only picks up literal string args; dynamic content (f-strings with vars,
    concatenations) is captured as best-effort by `ast.unparse` of the node.
    """
    out: list[Snippet] = []
    tree = ast.parse(path.read_text())

    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "_sys"):
            continue
        if len(node.args) < 2:
            continue
        label_node, content_node = node.args[0], node.args[1]

        # Label — should always be a literal
        if isinstance(label_node, ast.Constant) and isinstance(label_node.value, str):
            label = label_node.value
        else:
            label = ast.unparse(label_node)[:60]

        # Content — could be literal, joined string, f-string, etc.
        try:
            if isinstance(content_node, ast.Constant) and isinstance(content_node.value, str):
                text = content_node.value
            elif isinstance(content_node, ast.JoinedStr):
                # f-string: keep just the literal parts (skip {vars})
                text = "".join(
                    v.value for v in content_node.values
                    if isinstance(v, ast.Constant) and isinstance(v.value, str)
                )
            elif isinstance(content_node, ast.BinOp):
                # concat with +: try to render literal parts
                text = ast.unparse(content_node)
            elif isinstance(content_node, ast.Name):
                # variable reference (e.g. _memory_block_text) — skip, body is dynamic
                continue
            else:
                text = ast.unparse(content_node)
        except Exception:
            continue

        if not text or len(text) < MIN_TEXT_LEN:
            continue

        out.append(Snippet(kind="HC", source=label, text=text, extra={"file": str(path), "line": node.lineno}))

    return out


def extract_builtin_tools(path: Path) -> list[Snippet]:
    """Import BUILTIN_TOOLS from registry and split into description + per-param."""
    spec_path = str(path.parent)
    if spec_path not in sys.path:
        sys.path.insert(0, str(REPO_ROOT / "backend"))

    # Import without running the full app — registry is pure-data
    import importlib.util
    spec = importlib.util.spec_from_file_location("builtin_registry", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    tools = mod.BUILTIN_TOOLS

    out: list[Snippet] = []
    for t in tools:
        name = t["function"]["name"]
        desc = (t["function"].get("description") or "").strip()
        if desc and len(desc) >= MIN_TEXT_LEN:
            out.append(Snippet(
                kind="builtin_desc",
                source=f"builtin:{name}",
                text=desc,
                extra={"tool_name": name},
            ))
        params = t["function"].get("parameters", {}).get("properties", {})
        for p_name, p_def in params.items():
            p_desc = (p_def.get("description") or "").strip()
            if p_desc and len(p_desc) >= MIN_TEXT_LEN:
                out.append(Snippet(
                    kind="builtin_param",
                    source=f"builtin:{name}.{p_name}",
                    text=p_desc,
                    extra={"tool_name": name, "param": p_name},
                ))
    return out


def extract_db_sources(db_url: str) -> list[Snippet]:
    """Pull all instruction-bearing text from Postgres."""
    out: list[Snippet] = []
    conn = psycopg2.connect(db_url)
    try:
        cur = conn.cursor()

        # 1) tenant_tools description + config_json.function.description + params
        cur.execute("""
            SELECT id, tenant_id, name, description, config_json
            FROM tenant_tools
            WHERE deleted_at IS NULL AND is_active = true
        """)
        for tool_id, tenant_id, name, desc, cfg in cur.fetchall():
            # top-level description (often empty if config_json.function.description is set)
            if desc and len(desc.strip()) >= MIN_TEXT_LEN:
                out.append(Snippet(
                    kind="tool_desc",
                    source=f"tool[{tenant_id}]:{name}",
                    text=desc.strip(),
                    extra={"tenant_id": str(tenant_id), "tool_id": str(tool_id), "tool_name": name},
                ))
            # config_json.function.description
            fn = (cfg or {}).get("function") or {}
            fn_desc = (fn.get("description") or "").strip()
            if fn_desc and fn_desc != (desc or "").strip() and len(fn_desc) >= MIN_TEXT_LEN:
                out.append(Snippet(
                    kind="tool_desc",
                    source=f"tool[{tenant_id}]:{name}.function.description",
                    text=fn_desc,
                    extra={"tenant_id": str(tenant_id), "tool_id": str(tool_id), "tool_name": name},
                ))
            # per-parameter descriptions
            params = (fn.get("parameters") or {}).get("properties") or {}
            for p_name, p_def in params.items():
                p_desc = (p_def.get("description") or "").strip() if isinstance(p_def, dict) else ""
                if p_desc and len(p_desc) >= MIN_TEXT_LEN:
                    out.append(Snippet(
                        kind="tool_param",
                        source=f"tool[{tenant_id}]:{name}.params.{p_name}",
                        text=p_desc,
                        extra={"tenant_id": str(tenant_id), "tool_id": str(tool_id), "tool_name": name, "param": p_name},
                    ))

        # 2) builtin_tool_overrides
        cur.execute("SELECT tenant_id, tool_name, description FROM builtin_tool_overrides")
        for tenant_id, tool_name, description in cur.fetchall():
            if description and len(description.strip()) >= MIN_TEXT_LEN:
                out.append(Snippet(
                    kind="builtin_override",
                    source=f"override[{tenant_id}]:{tool_name}",
                    text=description.strip(),
                    extra={"tenant_id": str(tenant_id), "tool_name": tool_name},
                ))

        # 3) tenant_shell_configs: system_prompt, ontology_prompt, rules_text
        cur.execute("""
            SELECT tenant_id, system_prompt, ontology_prompt, rules_text
            FROM tenant_shell_configs
        """)
        for tenant_id, sys_prompt, ontology, rules in cur.fetchall():
            for fld, kind in [(sys_prompt, "system_prompt"), (ontology, "ontology"), (rules, "rules_text")]:
                if fld and len(fld.strip()) >= MIN_TEXT_LEN:
                    out.append(Snippet(
                        kind=kind,
                        source=f"shell[{tenant_id}]:{kind}",
                        text=fld.strip(),
                        extra={"tenant_id": str(tenant_id)},
                    ))

        # 4) tenant_api_keys.memory_prompt
        cur.execute("""
            SELECT id, tenant_id, name, memory_prompt FROM tenant_api_keys
            WHERE memory_prompt IS NOT NULL
        """)
        for key_id, tenant_id, name, memory_prompt in cur.fetchall():
            if memory_prompt and len(memory_prompt.strip()) >= MIN_TEXT_LEN:
                out.append(Snippet(
                    kind="memory_prompt",
                    source=f"api_key[{tenant_id}]:{name or key_id}",
                    text=memory_prompt.strip(),
                    extra={"tenant_id": str(tenant_id), "api_key_id": str(key_id)},
                ))

        # 5) tenant_api_key_groups.memory_prompt
        cur.execute("""
            SELECT id, tenant_id, name, memory_prompt FROM tenant_api_key_groups
            WHERE memory_prompt IS NOT NULL
        """)
        for grp_id, tenant_id, name, memory_prompt in cur.fetchall():
            if memory_prompt and len(memory_prompt.strip()) >= MIN_TEXT_LEN:
                out.append(Snippet(
                    kind="memory_prompt",
                    source=f"api_key_group[{tenant_id}]:{name or grp_id}",
                    text=memory_prompt.strip(),
                    extra={"tenant_id": str(tenant_id), "group_id": str(grp_id)},
                ))
    finally:
        conn.close()
    return out


# ---------------------------------------------------------------------------
# Embedding + similarity
# ---------------------------------------------------------------------------

def embed_text(text: str) -> np.ndarray:
    r = requests.post(
        f"{OLLAMA_URL}/api/embeddings",
        json={"model": EMBED_MODEL, "prompt": text},
        timeout=60,
    )
    r.raise_for_status()
    vec = np.array(r.json()["embedding"], dtype=np.float32)
    # normalize for cosine via dot product
    norm = np.linalg.norm(vec)
    if norm > 0:
        vec /= norm
    return vec


def embed_all(items: list[Snippet]) -> None:
    n = len(items)
    for i, item in enumerate(items, 1):
        if i % 10 == 0 or i == n:
            print(f"  embedding {i}/{n}...", file=sys.stderr)
        try:
            item.embedding = embed_text(item.text)
        except Exception as e:
            print(f"  WARN: embed failed for {item.source}: {e}", file=sys.stderr)


def _is_cross_tenant_copy(a: Snippet, b: Snippet) -> bool:
    """True when a and b are the SAME tool/param in DIFFERENT tenants.

    These are intentional cross-tenant copies (one tool shared across tenants).
    Not drift — exclude from the noise.
    """
    if a.kind != b.kind:
        return False
    if a.kind not in ("tool_desc", "tool_param", "builtin_override"):
        return False
    a_t = a.extra.get("tenant_id")
    b_t = b.extra.get("tenant_id")
    if not (a_t and b_t) or a_t == b_t:
        return False
    if a.extra.get("tool_name") != b.extra.get("tool_name"):
        return False
    if a.kind == "tool_param" and a.extra.get("param") != b.extra.get("param"):
        return False
    return True


def find_similar_pairs(
    items: list[Snippet],
    threshold: float,
    exclude_cross_tenant: bool = True,
) -> list[tuple[float, Snippet, Snippet]]:
    """Pairwise cosine on normalized vectors = dot product. Returns sorted desc by sim."""
    with_emb = [it for it in items if it.embedding is not None]
    if not with_emb:
        return []
    M = np.stack([it.embedding for it in with_emb])
    sim = M @ M.T  # (n, n)
    n = len(with_emb)
    out: list[tuple[float, Snippet, Snippet]] = []
    for i in range(n):
        for j in range(i + 1, n):
            s = float(sim[i, j])
            if s < threshold:
                continue
            if exclude_cross_tenant and _is_cross_tenant_copy(with_emb[i], with_emb[j]):
                continue
            out.append((s, with_emb[i], with_emb[j]))
    out.sort(key=lambda x: -x[0])
    return out


def cluster_pairs(pairs: list[tuple[float, Snippet, Snippet]]) -> list[list[Snippet]]:
    """Union-find on similar pairs to form clusters of related snippets.

    Avoids showing every pairwise combination — a group of 5 items with high
    mutual similarity becomes ONE cluster, not 10 pairs.
    """
    parent: dict[int, int] = {}

    def find(x: int) -> int:
        while parent.get(x, x) != x:
            parent[x] = parent.get(parent.get(x, x), parent.get(x, x))
            x = parent[x]
        parent.setdefault(x, x)
        return x

    def union(a: int, b: int) -> None:
        parent[find(a)] = find(b)

    nodes: dict[int, Snippet] = {}
    for _sim, a, b in pairs:
        ai, bi = id(a), id(b)
        nodes[ai] = a
        nodes[bi] = b
        union(ai, bi)

    clusters_by_root: dict[int, list[Snippet]] = {}
    for nid, snip in nodes.items():
        clusters_by_root.setdefault(find(nid), []).append(snip)

    return sorted(clusters_by_root.values(), key=lambda c: -len(c))


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

KIND_LABEL = {
    "HC": "🔒 Hardcoded",
    "builtin_desc": "🛠 Builtin tool",
    "builtin_param": "🛠 Builtin param",
    "builtin_override": "🛠 Builtin override",
    "tool_desc": "🔧 Tenant tool",
    "tool_param": "🔧 Tenant param",
    "ontology": "📘 Ontology",
    "system_prompt": "👤 System prompt",
    "rules_text": "📋 Rules",
    "memory_prompt": "🧠 Memory prompt",
}


def _cluster_kinds(cluster: list[Snippet]) -> str:
    kinds = {it.kind for it in cluster}
    return ", ".join(sorted(kinds))


def _cluster_priority(cluster: list[Snippet]) -> tuple[int, int]:
    """Sort key: lower priority value = more important to look at first.

    Highest priority (=0): clusters mixing HC with tool/ontology — rule overlap
    Next (=1): clusters within SAME tenant (drift inside one tenant's config)
    Next (=2): clusters mixing kinds (e.g. system_prompt + ontology)
    Lowest (=3): single-kind, multi-tenant (still noise but worth a look)
    Within same priority, larger cluster wins.
    """
    kinds = {it.kind for it in cluster}
    tenants = {it.extra.get("tenant_id") for it in cluster if it.extra.get("tenant_id")}
    has_hc = "HC" in kinds
    same_tenant = len(tenants) == 1 and len(tenants) > 0

    if has_hc and len(kinds) > 1:
        prio = 0
    elif same_tenant:
        prio = 1
    elif len(kinds) > 1:
        prio = 2
    else:
        prio = 3
    return (prio, -len(cluster))


def write_report(
    items: list[Snippet],
    pairs: list[tuple[float, Snippet, Snippet]],
    clusters: list[list[Snippet]],
    threshold: float,
    out_path: Path,
) -> None:
    lines: list[str] = []
    lines.append(f"# Instruction Audit Report\n")
    lines.append(f"_Generated: {os.popen('date -Iseconds').read().strip()}_\n")
    lines.append(f"\nTotal instruction snippets scanned: **{len(items)}**\n")
    lines.append(f"\nSimilarity threshold: **{threshold:.2f}** (cross-tenant copies excluded)\n")
    lines.append(f"\nPairs flagged: **{len(pairs)}** → grouped into **{len(clusters)}** clusters\n")
    lines.append("\n---\n")

    # Source breakdown
    by_kind: dict[str, int] = {}
    by_chars: dict[str, int] = {}
    for it in items:
        by_kind[it.kind] = by_kind.get(it.kind, 0) + 1
        by_chars[it.kind] = by_chars.get(it.kind, 0) + len(it.text)
    lines.append("\n## Source breakdown\n")
    lines.append("| Kind | Count | Total chars |")
    lines.append("|---|---:|---:|")
    for k in sorted(by_kind, key=lambda k: -by_chars[k]):
        lines.append(f"| {KIND_LABEL.get(k, k)} | {by_kind[k]} | {by_chars[k]:,} |")
    total_chars = sum(by_chars.values())
    lines.append(f"| **TOTAL** | **{sum(by_kind.values())}** | **{total_chars:,}** |")

    if not clusters:
        lines.append(f"\n## Clusters (>= {threshold:.2f})\n\n_None found._\n")
    else:
        # Sort by priority
        clusters = sorted(clusters, key=_cluster_priority)
        lines.append(f"\n## Clusters (>= {threshold:.2f}), sorted by priority\n")
        lines.append("\n_Priority: HC-overlap → same-tenant drift → mixed-kind → other._\n")

        for idx, cluster in enumerate(clusters, 1):
            prio = _cluster_priority(cluster)[0]
            prio_label = ["🔥 HC overlap", "⚠️ Same-tenant drift", "🔸 Mixed-kind", "ℹ️ Cross-tenant noise"][prio]
            tenants = {it.extra.get("tenant_id") for it in cluster if it.extra.get("tenant_id")}
            kinds = _cluster_kinds(cluster)
            lines.append(f"\n### Cluster {idx} — {prio_label} — {len(cluster)} items — kinds: `{kinds}`\n")
            if tenants:
                lines.append(f"_Tenants involved: {len(tenants)}_\n")
            for it in cluster:
                lines.append(f"\n**{KIND_LABEL.get(it.kind, it.kind)} — `{it.source}`**\n")
                lines.append("```\n" + it.text.strip() + "\n```\n")

    out_path.write_text("\n".join(lines))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--threshold", type=float, default=0.85,
                        help="Cosine similarity threshold for flagging duplicates")
    parser.add_argument("--out", type=Path, default=Path("audit_report.md"),
                        help="Output markdown report path")
    parser.add_argument("--no-db", action="store_true", help="Skip DB sources (HC + builtin only)")
    args = parser.parse_args()

    print("[1/4] Extracting HARDCODED-* blocks from pipeline.py...", file=sys.stderr)
    items = extract_hardcoded_blocks(PIPELINE_PY)
    print(f"      found {len(items)} HC snippets", file=sys.stderr)

    print("[2/4] Extracting BUILTIN_TOOLS from builtin_registry.py...", file=sys.stderr)
    builtins = extract_builtin_tools(BUILTIN_REGISTRY_PY)
    items.extend(builtins)
    print(f"      found {len(builtins)} builtin snippets", file=sys.stderr)

    if not args.no_db:
        print("[3/4] Extracting DB sources (tenant_tools, ontology, prompts, memory)...", file=sys.stderr)
        db_items = extract_db_sources(DB_URL)
        items.extend(db_items)
        print(f"      found {len(db_items)} DB snippets", file=sys.stderr)
    else:
        print("[3/4] Skipping DB sources (--no-db)", file=sys.stderr)

    print(f"[4/4] Embedding {len(items)} snippets via {EMBED_MODEL}...", file=sys.stderr)
    embed_all(items)

    pairs = find_similar_pairs(items, args.threshold, exclude_cross_tenant=True)
    print(f"Found {len(pairs)} similar pairs (cross-tenant copies excluded) above {args.threshold:.2f}", file=sys.stderr)

    clusters = cluster_pairs(pairs)
    print(f"Grouped into {len(clusters)} clusters", file=sys.stderr)

    write_report(items, pairs, clusters, args.threshold, args.out)
    print(f"Report written to {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
