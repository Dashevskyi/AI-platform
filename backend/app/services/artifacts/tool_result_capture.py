"""Capture tool execution results as first-class artifacts.

Why: tool results live only inside one round-trip. The next turn the model
can't see what `ping` returned, so it either re-runs the tool (good) or
hallucinates (bad). By turning substantial tool outputs into Artifact rows
(kind="tool-result") with embeddings, auto-grounding pulls them back into
the payload on the very next user message — same mechanism used for code
artifacts.

Skipped:
  • built-in retrieval tools (recall_chat, get_message, find_artifacts,
    get_artifact, recall_memory, search_kb, memory_save) — their output IS
    already retrieval, capturing it would be pointless and risks recursion;
  • attachment-search tools (search_attachment_*) — those queries hit the
    attachment chunk index, not raw data;
  • short outputs (< MIN_CHARS) — nothing useful to ground later.
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone

from app.core.config import settings as app_settings
from app.core.database import async_session
from app.models.artifact import Artifact
from app.providers.factory import get_provider
from app.services.memory.embedder import _resolve_embedding_model
from app.services.tools.builtin_registry import BUILTIN_TOOL_NAMES

logger = logging.getLogger(__name__)


# Below this size the output is small enough that re-calling the tool is
# cheaper than maintaining an artifact. Above it, store + ground.
MIN_TOOL_RESULT_CHARS = 100
# Cap on what we keep in `content` — keeps DB rows bounded. Long log dumps
# still occasionally exceed this; that's a known trade-off vs. storage cost.
MAX_TOOL_RESULT_CHARS = 12000


def _should_capture(tool_name: str) -> bool:
    """Decide whether this tool's result is worth promoting to an artifact."""
    if not tool_name:
        return False
    if tool_name in BUILTIN_TOOL_NAMES:
        return False
    if tool_name.startswith("search_attachment_"):
        return False
    return True


def _short_args(arguments: dict | str | None, max_chars: int = 120) -> str:
    """Short, human-readable summary of call arguments for the artifact label."""
    if arguments is None:
        return ""
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except (json.JSONDecodeError, TypeError):
            return arguments[:max_chars]
    if not isinstance(arguments, dict):
        return str(arguments)[:max_chars]
    # Drop runtime context if it leaked in.
    parts: list[str] = []
    for k, v in arguments.items():
        if k.startswith("_"):
            continue
        if isinstance(v, (list, tuple)):
            v_repr = f"[{len(v)} items]" if len(v) > 3 else json.dumps(v, ensure_ascii=False)
        elif isinstance(v, dict):
            v_repr = f"{{{len(v)} keys}}"
        else:
            v_repr = str(v)
        if len(v_repr) > 40:
            v_repr = v_repr[:40] + "…"
        parts.append(f"{k}={v_repr}")
        if sum(len(p) for p in parts) > max_chars:
            break
    return ", ".join(parts)[:max_chars]


def _build_label(tool_name: str, arguments: dict | str | None) -> str:
    args_str = _short_args(arguments)
    if args_str:
        return f"Результат {tool_name}({args_str})"
    return f"Результат {tool_name}"


async def capture_tool_result_as_artifact(
    *,
    tenant_id: uuid.UUID,
    chat_id: uuid.UUID,
    user_message_id: uuid.UUID | None,
    tool_name: str,
    arguments: dict | str | None,
    output: str,
) -> uuid.UUID | None:
    """Persist a tool result as an Artifact row in its own session. Returns
    the new artifact id, or None when we didn't capture (or failed silently)."""
    if not _should_capture(tool_name):
        return None
    if not output or len(output) < MIN_TOOL_RESULT_CHARS:
        return None

    content = output if len(output) <= MAX_TOOL_RESULT_CHARS else output[:MAX_TOOL_RESULT_CHARS] + "\n…[усечено]"
    label = _build_label(tool_name, arguments)

    # Use a dedicated short-lived session — the pipeline's session stays
    # open across the LLM call, so we MUST NOT hold row-locks here.
    try:
        async with async_session() as db:
            embed_model = await _resolve_embedding_model(tenant_id, db)
            vec: list[float] | None = None
            if embed_model:
                try:
                    provider = get_provider(
                        "ollama",
                        app_settings.OLLAMA_BASE_URL or "http://localhost:11434",
                    )
                    embed_input = f"{label}\n{content[:1500]}"
                    vectors = await provider.embed(embed_input, embed_model)
                    if vectors:
                        vec = vectors[0]
                except Exception:
                    logger.exception("[tool-result-capture] embed failed")

            art = Artifact(
                tenant_id=tenant_id,
                chat_id=chat_id,
                source_message_id=user_message_id,
                kind="tool-result",
                label=label[:500],
                lang=None,
                content=content,
                tokens_estimate=max(1, len(content) // 4),
                version=1,
                parent_artifact_id=None,
                last_referenced_at=datetime.now(timezone.utc),
            )
            if vec is not None:
                art.embedding = vec
                art.embedding_model = embed_model
            db.add(art)
            await db.commit()
            await db.refresh(art)
            logger.info(
                "[tool-result-capture] saved %s as artifact %s (%d chars)",
                tool_name, art.id, len(content),
            )
            return art.id
    except Exception:
        logger.exception("[tool-result-capture] failed for tool=%s", tool_name)
        return None
