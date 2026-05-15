"""Artifact extraction from assistant messages.

Pipeline:
  1. Deterministic — regex over fenced code blocks in the assistant content.
     A block becomes an artifact only if it crosses a size threshold (so we
     don't promote inline `code` words to first-class entities).
  2. Optional LLM labeling — given the list of extracted blocks, ask the model
     to produce {kind, label} for each by index. If the LLM call fails, we
     fall back to a heuristic kind/label derived from the fence language and
     the first content line.
  3. Persist — one Artifact row per block, with embedding for semantic search.

The actual content of an artifact is always taken from the regex-extracted
block, never from what the LLM says. The LLM is only allowed to NAME the
blocks, not invent them. This is what keeps the artifact store grounded.
"""
from __future__ import annotations

import json
import logging
import re
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings as app_settings
from app.models.artifact import Artifact
from app.models.tenant_shell_config import TenantShellConfig
from app.providers.factory import get_provider
from app.services.memory.embedder import _resolve_embedding_model

logger = logging.getLogger(__name__)


# Anything shorter than this is treated as an inline snippet, not an artifact.
# Char-based only — a single-line dense SQL or curl is still a real artifact.
MIN_ARTIFACT_CHARS = 80

# Hard cap on how many artifacts we promote from a single message — protects
# against pathological cases (model dumps 50 fences in one reply).
MAX_ARTIFACTS_PER_MESSAGE = 8

# How much of the artifact body goes into the embedding text. Embeddings are
# capped by the model anyway; this just keeps the input modest.
EMBED_INPUT_HEAD_CHARS = 1500


_FENCE_RE = re.compile(r"```([a-zA-Z0-9_+\-]*)\n(.*?)```", re.DOTALL)


# A new artifact is treated as a NEW VERSION of an existing one when:
#  - same kind
#  - same chat
#  - cosine similarity between embeddings ≥ this threshold
#  - the prior artifact was created within this many seconds
# Tuning notes: 0.80 is loose enough to catch "edit my script", but tight
# enough that a brand-new unrelated bash-script (with different label/intent)
# won't accidentally become "v2" of something old.
VERSION_SIMILARITY_THRESHOLD = 0.80
VERSION_LOOKBACK_SECONDS = 60 * 60 * 24  # one day


# Heuristic: language token from the fence → canonical artifact kind.
_LANG_TO_KIND: dict[str, str] = {
    "bash": "bash-script",
    "sh": "bash-script",
    "shell": "bash-script",
    "zsh": "bash-script",
    "python": "python-script",
    "py": "python-script",
    "sql": "sql-query",
    "yaml": "yaml-config",
    "yml": "yaml-config",
    "json": "json-config",
    "nginx": "nginx-config",
    "dockerfile": "dockerfile",
    "ini": "ini-config",
    "toml": "toml-config",
    "html": "code",
    "css": "code",
    "js": "code",
    "ts": "code",
    "tsx": "code",
    "jsx": "code",
    "go": "code",
    "rust": "code",
    "java": "code",
    "c": "code",
    "cpp": "code",
    "": "code",
}


def _normalize_kind(lang: str | None) -> str:
    """Map a fence language tag to a canonical artifact kind."""
    if not lang:
        return "code"
    key = lang.strip().lower()
    return _LANG_TO_KIND.get(key, "code")


def _is_significant(body: str) -> bool:
    """Decide whether a fenced block is meaty enough to become an artifact."""
    if not body:
        return False
    return len(body.strip()) >= MIN_ARTIFACT_CHARS


def _heuristic_label(kind: str, lang: str | None, body: str) -> str:
    """Pick a short label when the LLM didn't provide one. Looks at:
    - shebang line for scripts ('#!/bin/bash')
    - first comment / docstring line
    - first non-empty source line, truncated
    """
    lines = [ln.strip() for ln in body.strip().splitlines() if ln.strip()]
    if not lines:
        return f"{kind} (без заголовка)"
    first = lines[0]
    if first.startswith("#!"):
        # Shebang — use the second line if it's a comment/doc.
        if len(lines) > 1:
            second = lines[1]
            if second.startswith(("#", "//", "--", "/*")):
                return second.lstrip("#/*- ").strip()[:200] or first[:200]
    # First comment-like line is usually a self-description.
    if first.startswith(("#", "//", "--", "/*", '"""', "'''")):
        cleaned = first.lstrip("#/*-\"' ").strip()
        if cleaned:
            return cleaned[:200]
    return first[:200]


def _estimate_tokens(content: str) -> int:
    """Rough char→token approximation. Good enough for budget math."""
    return max(1, len(content) // 4)


def extract_fenced_blocks(content: str) -> list[dict]:
    """Pure parser: returns [{lang, body, span: (start, end)}] for every
    significant fenced block. No LLM, no DB. The single source of truth for
    *what counts* as an artifact."""
    if not content:
        return []
    out: list[dict] = []
    for m in _FENCE_RE.finditer(content):
        lang = (m.group(1) or "").strip().lower() or None
        body = m.group(2)
        if not _is_significant(body):
            continue
        out.append({"lang": lang, "body": body.rstrip(), "span": m.span()})
        if len(out) >= MAX_ARTIFACTS_PER_MESSAGE:
            break
    return out


_LABEL_PROMPT = """Дан ассистентский ответ, из которого извлечены {n} fenced-блоков (по порядку, индексы 0..{last_idx}). Для каждого блока верни короткий ярлык (label) на русском, до 8 слов, отражающий ЧТО это и для чего.

Не описывай содержимое подробно — нужен только заголовок-«название артефакта», как папка в репозитории.

Верни СТРОГО JSON-массив той же длины, без обёрток, без текста до/после:
[{{"label": "..."}}, {{"label": "..."}}, ...]

Блоки (с языком в скобках):
{blocks_text}

JSON:"""


def _parse_labels_json(text: str, expected: int) -> list[str | None]:
    """Best-effort parse of the LLM label response. Returns a list of length
    `expected`, with None for slots we couldn't recover."""
    if not text:
        return [None] * expected
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        parsed = json.loads(cleaned)
        if isinstance(parsed, list):
            out: list[str | None] = []
            for item in parsed[:expected]:
                if isinstance(item, dict):
                    label = item.get("label")
                    out.append(str(label).strip() if isinstance(label, str) and label.strip() else None)
                elif isinstance(item, str) and item.strip():
                    out.append(item.strip())
                else:
                    out.append(None)
            # Pad to expected length.
            while len(out) < expected:
                out.append(None)
            return out
    except json.JSONDecodeError:
        pass
    return [None] * expected


async def _label_blocks_via_llm(
    blocks: list[dict],
    *,
    provider,
    model_name: str,
    language_pin_message: dict | None,
) -> list[str | None]:
    """Ask the LLM to produce a label per block. Returns Nones on failure —
    callers handle the heuristic fallback per slot."""
    if not blocks:
        return []
    blocks_text_lines: list[str] = []
    for i, block in enumerate(blocks):
        body = block["body"]
        head = body[:600] + ("..." if len(body) > 600 else "")
        blocks_text_lines.append(f"[{i}] (lang={block['lang'] or '?'}):\n{head}\n")
    blocks_text = "\n".join(blocks_text_lines)
    prompt = _LABEL_PROMPT.format(
        n=len(blocks),
        last_idx=len(blocks) - 1,
        blocks_text=blocks_text,
    )
    messages: list[dict] = []
    if language_pin_message is not None:
        messages.append(language_pin_message)
    messages.append({"role": "user", "content": prompt})
    try:
        resp = await provider.chat_completion(
            messages=messages,
            model=model_name,
            temperature=0.1,
            max_tokens=400,
        )
        return _parse_labels_json(resp.content or "", expected=len(blocks))
    except Exception as e:
        logger.warning(
            "[artifact-extractor] LLM labeling failed: %s: %r; falling back to heuristics",
            type(e).__name__, e,
        )
        return [None] * len(blocks)


async def _find_parent_artifact(
    *,
    db: AsyncSession,
    tenant_id: uuid.UUID,
    chat_id: uuid.UUID,
    kind: str,
    new_vec: list[float],
) -> tuple[uuid.UUID | None, int]:
    """Detect whether the freshly-extracted artifact is a NEW VERSION of an
    existing one in the same chat. Returns (parent_id, parent_version) or
    (None, 0). Uses pgvector cosine distance (1 - distance = similarity).
    """
    from sqlalchemy import text as sa_text
    vec_str = "[" + ",".join(f"{float(x):.6f}" for x in new_vec) + "]"
    sql = sa_text(
        f"""
        SELECT id, version, 1 - (embedding <=> CAST(:qvec AS vector)) AS similarity
        FROM artifacts
        WHERE tenant_id = :tid
          AND chat_id = :cid
          AND kind = :kind
          AND deleted_at IS NULL
          AND embedding IS NOT NULL
          AND created_at >= NOW() - INTERVAL '{VERSION_LOOKBACK_SECONDS} seconds'
        ORDER BY embedding <=> CAST(:qvec AS vector)
        LIMIT 1
        """
    )
    row = (await db.execute(sql, {
        "tid": tenant_id, "cid": chat_id, "kind": kind, "qvec": vec_str,
    })).fetchone()
    if not row or row.similarity is None or row.similarity < VERSION_SIMILARITY_THRESHOLD:
        return None, 0
    logger.info(
        "[artifact-extractor] version-detect: linking new artifact as v%d of %s (sim=%.3f)",
        row.version + 1, row.id, row.similarity,
    )
    return row.id, row.version


async def extract_and_save_artifacts(
    *,
    db: AsyncSession,
    tenant_id: uuid.UUID,
    chat_id: uuid.UUID,
    source_message_id: uuid.UUID,
    assistant_content: str,
    provider,
    model_name: str,
    response_language: str | None,
) -> list[Artifact]:
    """Top-level entry point. Extract fenced blocks from assistant_content,
    label them, embed them, persist as Artifact rows. Returns the persisted
    rows (caller commits)."""
    blocks = extract_fenced_blocks(assistant_content)
    if not blocks:
        return []

    # Build language pin lazily so we don't import at module top.
    from app.services.llm.language import build_language_pin_message
    pin = build_language_pin_message(response_language)

    labels = await _label_blocks_via_llm(
        blocks,
        provider=provider,
        model_name=model_name,
        language_pin_message=pin,
    )

    # Embedding model is resolved once for all artifacts.
    embed_model = await _resolve_embedding_model(tenant_id, db)
    embed_provider = None
    if embed_model:
        try:
            embed_provider = get_provider(
                "ollama",
                app_settings.OLLAMA_BASE_URL or "http://localhost:11434",
            )
        except Exception:
            logger.exception("[artifact-extractor] failed to init embed provider")
            embed_provider = None

    created: list[Artifact] = []
    for i, block in enumerate(blocks):
        kind = _normalize_kind(block["lang"])
        label = (labels[i] if i < len(labels) else None) or _heuristic_label(kind, block["lang"], block["body"])
        content = block["body"]

        # Embed FIRST so we can detect parent. If no embedding model is
        # configured, version always starts at 1 — no detection possible.
        new_vec: list[float] | None = None
        if embed_provider and embed_model:
            try:
                embed_input = f"{label}\n{content[:EMBED_INPUT_HEAD_CHARS]}"
                vectors = await embed_provider.embed(embed_input, embed_model)
                if vectors:
                    new_vec = vectors[0]
            except Exception:
                logger.exception("[artifact-extractor] embed failed for artifact %d", i)

        # Version auto-detect: find the freshest same-kind artifact in this
        # chat whose embedding is highly similar to the new one (= the user
        # asked to edit/extend, not to make a new thing). If found, link as
        # a new version of it.
        parent_id: uuid.UUID | None = None
        version = 1
        if new_vec is not None:
            parent_id, parent_version = await _find_parent_artifact(
                db=db,
                tenant_id=tenant_id,
                chat_id=chat_id,
                kind=kind,
                new_vec=new_vec,
            )
            if parent_id is not None:
                version = parent_version + 1

        art = Artifact(
            tenant_id=tenant_id,
            chat_id=chat_id,
            source_message_id=source_message_id,
            kind=kind,
            label=label[:500],
            lang=block["lang"],
            content=content,
            tokens_estimate=_estimate_tokens(content),
            version=version,
            parent_artifact_id=parent_id,
            last_referenced_at=datetime.now(timezone.utc),
        )
        if new_vec is not None:
            art.embedding = new_vec
            art.embedding_model = embed_model

        db.add(art)
        created.append(art)

    await db.flush()
    logger.info(
        "[artifact-extractor] saved %d artifacts for message %s (kinds: %s)",
        len(created),
        source_message_id,
        ",".join(a.kind for a in created),
    )
    return created
