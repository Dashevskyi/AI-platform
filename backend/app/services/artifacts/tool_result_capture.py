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
    """Heuristic fallback when LLM labeling isn't available — just echoes
    tool name + a compact args summary."""
    args_str = _short_args(arguments)
    if args_str:
        return f"Результат {tool_name}({args_str})"
    return f"Результат {tool_name}"


_TOOL_RESULT_LABEL_PROMPT = (
    "Сожми результат вызова tool в один короткий заголовок (30-90 символов) "
    "на языке пользователя. Включи: тематику + ключевой факт результата "
    "(сколько строк/что найдено/ошибка/итог). Без кавычек, без префиксов "
    "«Результат», «Tool». Только заголовок одной строкой.\n\n"
    "Tool: {tool_name}\n"
    "Аргументы: {args_str}\n\n"
    "Результат (фрагмент):\n{content_head}\n\n"
    "Заголовок:"
)


async def _llm_label_for_tool_result(
    *,
    tenant_id: uuid.UUID,
    tool_name: str,
    arguments: dict | str | None,
    content: str,
) -> str | None:
    """Ask the cheapest/fastest tenant model to produce a one-line description
    of this tool result. Returns None on failure — caller falls back to the
    heuristic `_build_label`. Capped to 1500 chars of content + 200 chars of
    args to keep this cheap; ~50 token output."""
    if not content:
        return None
    try:
        from sqlalchemy import select as _select
        from app.models.tenant_shell_config import TenantShellConfig
        from app.providers.factory import get_provider
        from app.core.security import decrypt_value

        async with async_session() as db:
            cfg = (await db.execute(
                _select(TenantShellConfig).where(TenantShellConfig.tenant_id == tenant_id)
            )).scalar_one_or_none()
            if not cfg:
                return None
            model_name = (cfg.summary_model_name or cfg.model_name or "").strip()
            if not model_name:
                return None
            api_key = decrypt_value(cfg.provider_api_key_enc) if cfg.provider_api_key_enc else None
            provider = get_provider(cfg.provider_type, cfg.provider_base_url, api_key)

        # Compose prompt
        args_summary = _short_args(arguments, max_chars=200) or "(нет аргументов)"
        head = content[:1500]
        prompt = _TOOL_RESULT_LABEL_PROMPT.format(
            tool_name=tool_name,
            args_str=args_summary,
            content_head=head,
        )
        resp = await provider.chat_completion(
            messages=[{"role": "user", "content": prompt}],
            model=model_name,
            temperature=0.1,
            max_tokens=80,
            # Disable thinking: tool-result labeling is one short line — reasoning
            # burns the token budget and leaves resp.content empty on Qwen3.
            extra_body={"chat_template_kwargs": {"enable_thinking": False}},
        )
        label = (resp.content or "").strip().splitlines()[0].strip()
        # Strip common pre/suffixes the model might add despite instruction.
        for prefix in ("Заголовок:", "Результат:", "«", "\"", "'"):
            if label.startswith(prefix):
                label = label[len(prefix):].lstrip()
        for suffix in ("»", "\"", "'"):
            if label.endswith(suffix):
                label = label[:-len(suffix)].rstrip()
        # Sanity bounds
        if not label or len(label) > 200:
            return None
        return label
    except Exception:
        logger.warning("[tool-result-capture] LLM labeling failed; falling back to heuristic", exc_info=True)
        return None


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
    # Prefer LLM-generated semantic label (helps semantic grounding because
    # the embedding text includes the label). Fall back to the heuristic
    # "Результат tool(args)" when the LLM call fails.
    llm_label = await _llm_label_for_tool_result(
        tenant_id=tenant_id, tool_name=tool_name, arguments=arguments, content=content,
    )
    label = llm_label or _build_label(tool_name, arguments)

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
