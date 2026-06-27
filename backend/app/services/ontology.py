"""Structured ontology ⇄ flat text.

`ontology_json` (source of truth, edited via the structured/graph UI) is rendered
to the plain `ontology_prompt` the LLM reads. The decision-logic section is a
GRAPH (nodes + branch/goto links); it's flattened into labelled blocks with
"→ перейти к «…»" references — dedups shared branches and is cycle-safe (we never
inline-expand a target, only reference it by label).

Schema (ontology_json):
  {"version":1, "sections":[ <section>, ... ]}
  glossary : {type, title, items:[{term, definition}]}
  entities : {type, title, entities:[{name, fields:[{name, type, description}]}]}
  relations: {type, title, items:[{from, relation, to}]}
  logic    : {type, title, graph:{start:<id|None>, nodes:{<id>:<node>}}}
             node = condition {type, label, branches:[{case, next:<id|None>}]}
                  | action    {type, label?, tool?, hint?, next:<id|None>}
                  | note      {type, label?, text?, next:<id|None>}
                  | ref       {type, label?, flowId:<flow id>, next:<id|None>}
  examples : {type, title, items:[{query, expected_tool?, note?}]}
  freeform : {type, title, text}
"""
from __future__ import annotations

import re


def _node_label(nid: str, node: dict, flow_names: dict[str, str] | None = None) -> str:
    if node.get("type") == "ref":
        fid = str(node.get("flowId") or "").strip()
        if fid and flow_names and flow_names.get(fid):
            base = flow_names[fid]
            if node.get("label"):
                return f"{str(node['label']).strip()} → {base}"
            return base
    return (node.get("label") or node.get("tool") or nid).strip() or nid


def _action_text(node: dict, flow_names: dict[str, str] | None = None) -> str:
    if node.get("type") == "ref":
        fid = str(node.get("flowId") or "").strip()
        fname = (flow_names or {}).get(fid, fid) if fid else ""
        parts = []
        if node.get("label"):
            parts.append(str(node["label"]).strip())
        if fname:
            parts.append(f"выполнить сценарий «{fname}»")
        return " — ".join(parts) if parts else ""
    parts = []
    if node.get("label"):
        parts.append(str(node["label"]).strip())
    if node.get("tool"):
        t = str(node["tool"]).strip()
        hint = str(node.get("hint") or "").strip()
        parts.append(f"вызови `{t}`" + (f" ({hint})" if hint else ""))
    elif node.get("hint"):
        parts.append(str(node["hint"]).strip())
    return " — ".join(parts) if parts else ""


def _succ(node: dict) -> list[str]:
    out = []
    if node.get("type") == "condition":
        for b in node.get("branches") or []:
            if b.get("next"):
                out.append(b["next"])
    elif node.get("next"):
        out.append(node["next"])
    return out


def serialize_graph(graph: dict, flow_names: dict[str, str] | None = None) -> str:
    nodes: dict = graph.get("nodes") or {}
    if not nodes:
        return ""
    start = graph.get("start") if graph.get("start") in nodes else None

    indeg = {nid: 0 for nid in nodes}
    for n in nodes.values():
        for t in _succ(n):
            if t in indeg:
                indeg[t] += 1

    # Block heads: start, anything with in-degree ≠ 1 (joins/roots), and every
    # condition branch target (so branches reference a stable labelled block).
    heads: set[str] = set()
    if start:
        heads.add(start)
    for nid, n in nodes.items():
        if indeg.get(nid, 0) != 1:
            heads.add(nid)
        if n.get("type") == "condition":
            for b in n.get("branches") or []:
                if b.get("next") in nodes:
                    heads.add(b["next"])

    ordered: list[str] = []
    if start:
        ordered.append(start)
    for nid in nodes:
        if nid in heads and nid not in ordered:
            ordered.append(nid)

    emitted: set[str] = set()
    blocks: list[str] = []

    def lbl(nid: str) -> str:
        return _node_label(nid, nodes.get(nid, {}), flow_names)

    def emit_block(head: str) -> str:
        lines: list[str] = [f"[{lbl(head)}]"]
        nid = head
        first = True
        while nid and nid in nodes:
            if not first and nid in heads:
                lines.append(f"  → перейти к «{lbl(nid)}»")
                break
            n = nodes[nid]
            emitted.add(nid)
            if n.get("type") == "condition":
                if n.get("label") and (first or True):
                    lines.append(f"  {str(n['label']).strip()}:")
                for b in (n.get("branches") or []):
                    case = str(b.get("case") or "?").strip()
                    nxt = b.get("next")
                    if nxt and nxt in nodes:
                        lines.append(f"    • {case} → перейти к «{lbl(nxt)}»")
                    else:
                        lines.append(f"    • {case} → (конец)")
                break
            else:
                txt = _action_text(n, flow_names) or (str(n.get("text") or "").strip())
                if txt:
                    lines.append(("  " + txt) if not first else f"  {txt}")
                nxt = n.get("next")
                if nxt and nxt in nodes and nxt in heads:
                    lines.append(f"  → перейти к «{lbl(nxt)}»")
                    break
                nid = nxt
            first = False
        return "\n".join(lines)

    for head in ordered:
        if head not in emitted:
            blocks.append(emit_block(head))
    # any unreachable leftovers — don't lose them
    for nid in nodes:
        if nid not in emitted:
            blocks.append(emit_block(nid))
    return "\n\n".join(b for b in blocks if b.strip())


def serialize(ontology_json: dict | None) -> str:
    """Render structured ontology → flat text for the LLM."""
    if not isinstance(ontology_json, dict):
        return ""
    out: list[str] = []
    for sec in ontology_json.get("sections") or []:
        if not isinstance(sec, dict):
            continue
        typ = sec.get("type")
        title = str(sec.get("title") or "").strip()
        if typ == "glossary":
            body = [f"• {str(i.get('term','')).strip()} — {str(i.get('definition','')).strip()}"
                    for i in (sec.get("items") or []) if i.get("term")]
        elif typ == "entities":
            body = []
            for e in sec.get("entities") or []:
                body.append(f"### {str(e.get('name','')).strip()}")
                for f in e.get("fields") or []:
                    body.append(f"{str(f.get('name','')).strip()} ({str(f.get('type','')).strip()}): "
                                f"{str(f.get('description','')).strip()}")
        elif typ == "relations":
            body = [f"• {str(i.get('from','')).strip()} → {str(i.get('relation','')).strip()} → "
                    f"{str(i.get('to','')).strip()}" for i in (sec.get("items") or []) if i.get("from")]
        elif typ == "logic":
            # A logic section holds one or more NAMED decision flows (each its
            # own graph + start). Back-compat: an old single-graph section is
            # treated as one unnamed flow.
            flows = sec.get("flows")
            if not flows and sec.get("graph"):
                flows = [{"name": "", "graph": sec.get("graph")}]
            flow_names = {
                str(fl.get("id")): str(fl.get("name") or fl.get("id") or "").strip() or str(fl.get("id"))
                for fl in (flows or [])
                if isinstance(fl, dict) and fl.get("id")
            }
            body = []
            for fl in flows or []:
                if not isinstance(fl, dict):
                    continue
                g = serialize_graph(fl.get("graph") or {}, flow_names)
                if not g:
                    continue
                name = str(fl.get("name") or "").strip()
                body.append((f"### {name}\n{g}") if name else g)
        elif typ == "examples":
            body = []
            for i in sec.get("items") or []:
                q = str(i.get("query", "")).strip()
                if not q:
                    continue
                tool = str(i.get("expected_tool") or "").strip()
                note = str(i.get("note") or "").strip()
                body.append(f"• «{q}»" + (f" → {tool}" if tool else "") + (f" ({note})" if note else ""))
        elif typ == "freeform":
            body = [str(sec.get("text") or "").strip()]
        else:
            body = []
        body = [b for b in body if b.strip()]
        if not body and not title:
            continue
        block = (f"## {title}\n" if title else "") + "\n".join(body)
        out.append(block.strip())
    return "\n\n".join(out).strip()


# ----- bootstrap parser: existing flat ontology_prompt → structured json -----

def _classify(title: str) -> str:
    t = title.lower()
    if any(k in t for k in ("терминолог", "глоссар", "термін")):
        return "glossary"
    if any(k in t for k in ("сутніст", "сущност", "schema", "сутно")):
        return "entities"
    if any(k in t for k in ("зв'язк", "связи", "relation", "звязк")):
        return "relations"
    if any(k in t for k in ("рішень", "decision", "логик", "матриц", "решени")):
        return "logic"
    if any(k in t for k in ("приклад", "example", "примеры", "карт")):
        return "examples"
    return "freeform"


_TOOL_RE = re.compile(r"\b([a-z][a-z0-9]*_[a-z0-9_]+)\s*\(?|\b([a-z][a-z0-9_]{3,})\s*\(")


def _parse_logic_line(line: str, nid) -> dict | None:
    """One decision-matrix line → a named flow. Handles both
    «N) интент → tool(args)» and «IF/ЕСЛИ условие → THEN действие(tool)»."""
    s = line.strip().lstrip("•-*– ").strip()
    s = re.sub(r"^\d+[\).]\s*", "", s)                 # strip "3) " / "4. "
    if len(s) < 4:
        return None
    parts = re.split(r"\s*(?:→|->)\s*", s, maxsplit=1)
    intent = parts[0].strip()
    action_txt = (parts[1].strip() if len(parts) > 1 else s).strip()
    action_txt = re.sub(r"^(?:THEN|ТОГДА)\s+", "", action_txt, flags=re.I).strip()
    cond = re.match(r"(?:IF|ЕСЛИ)\s+(.+)", intent, re.I)
    # tool: prefer the token that is actually CALLED (`name(`), else a snake_case token
    m = re.search(r"\b([a-z][a-z0-9_]{2,})\s*\(", action_txt) or \
        re.search(r"\b([a-z][a-z0-9]*_[a-z0-9_]+)\b", action_txt)
    tool = m.group(1) if m else None
    pm = re.search(r"\(([^)]*)\)", action_txt)
    hint = (pm.group(1).strip() if pm else re.sub(r"^[a-z_]+\s*[—:-]?\s*", "", action_txt)).strip()[:160]
    name = (cond.group(1).strip() if cond else intent)[:90] or action_txt[:90]
    if not tool and not cond and len(action_txt) < 6:
        return None
    a = nid()
    fid = "fl" + nid()
    action_node = {"type": "action", "label": "" if cond else name, "tool": tool, "hint": hint, "next": None}
    if cond:
        c = nid()
        return {"id": fid, "name": name, "graph": {"start": c, "nodes": {
            c: {"type": "condition", "label": cond.group(1).strip()[:90], "branches": [{"case": "да", "next": a}]},
            a: action_node}}}
    return {"id": fid, "name": name, "graph": {"start": a, "nodes": {a: action_node}}}


def parse_text(text: str) -> dict:
    """Best-effort: split a flat ontology into typed sections. Anything that
    doesn't match a known shape lands in a freeform section (never lost). The
    logic section is parsed into a simple linear graph of condition nodes; the
    admin then re-wires it visually."""
    if not text or not text.strip():
        return {"version": 1, "sections": []}
    lines = text.splitlines()
    hdr = re.compile(r"^\s*(#{1,4}\s+.+|[0-9]+\.\s+\S.+)$")
    blocks: list[dict] = []
    cur = {"title": "", "lines": []}
    for ln in lines:
        if hdr.match(ln) and not re.match(r"^\s*[0-9]+\.\s+(IF|ЕСЛИ|Приклад)", ln, re.I):
            blocks.append(cur)
            cur = {"title": re.sub(r"^[#\s0-9.]+", "", ln).strip(), "lines": []}
        else:
            cur["lines"].append(ln)
    blocks.append(cur)

    sections: list[dict] = []
    _gid = [0]

    def nid() -> str:
        _gid[0] += 1
        return f"n{_gid[0]}"

    for b in blocks:
        title = b["title"]
        body_lines = b["lines"]
        body = "\n".join(body_lines).strip()
        if not title and not body:
            continue
        kind = _classify(title)
        ent = re.match(r"(?:сутніст[ьґ]|сущность|entity)\s*:\s*(.+)", title, re.I)
        if ent or (kind == "entities" and re.search(r"\(\w+\):", body)):
            name = ent.group(1).strip() if ent else title
            fields = []
            for ln in body_lines:
                fm = re.match(r"\s*([A-Za-zА-Яа-я_/]+)\s*\(([^)]+)\):\s*(.+)", ln.strip())
                if fm:
                    fields.append({"name": fm.group(1), "type": fm.group(2), "description": fm.group(3).strip()})
            if fields:
                sections.append({"type": "entities", "title": title, "entities": [{"name": name, "fields": fields}]})
                continue
        if kind == "glossary":
            items = []
            for ln in body_lines:
                gm = re.match(r"\s*[•\-\*]\s*(.+?)\s*[—:–]\s*(.+)", ln.strip())
                if gm:
                    items.append({"term": gm.group(1).strip(), "definition": gm.group(2).strip()})
            if items:
                sections.append({"type": "glossary", "title": title, "items": items})
                continue
        if kind == "relations":
            items = []
            for ln in body_lines:
                s = ln.strip()
                if not s:
                    continue
                parts = re.split(r"\s*(?:→|->)\s*", s)
                if len(parts) >= 2:
                    items.append({"from": parts[0], "relation": parts[1] if len(parts) == 2 else "→".join(parts[1:-1]),
                                  "to": parts[-1]})
            if items:
                sections.append({"type": "relations", "title": title, "items": items})
                continue
        if kind == "logic":
            # Join continuation lines ("→ action" under an intent line) into the
            # intent above them, so one matrix entry = one flow.
            merged: list[str] = []
            for ln in body_lines:
                st = ln.strip()
                if (st.startswith("→") or st.startswith("->")) and merged:
                    merged[-1] = merged[-1].rstrip() + " " + st
                else:
                    merged.append(ln)
            flows = []
            for ln in merged:
                fl = _parse_logic_line(ln, nid)
                if fl:
                    flows.append(fl)
            if flows:
                sections.append({"type": "logic", "title": title, "flows": flows})
                continue
        # fallback
        sections.append({"type": "freeform", "title": title, "text": body})
    return {"version": 1, "sections": sections}
