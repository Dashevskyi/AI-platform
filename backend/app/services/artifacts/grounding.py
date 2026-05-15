"""Artifact auto-grounding — deterministic source of facts.

The point: when a user asks about something they made earlier ("the script",
"that config", "продолжи код"), we MUST put the original artifact content into
the LLM payload, not hope the model recalls it from summaries. Resumes can
hallucinate; an artifact row cannot — it stores verbatim text.

Two retrieval signals are combined:
  1. Semantic similarity between user_content embedding and artifact embedding
     (label + content head are embedded at creation time).
  2. Recency — anything referenced within RECENT_WINDOW_SECONDS goes in
     regardless of similarity. This handles "продолжи" / "поправь" / "дальше"
     where the query has zero semantic overlap with the artifact label.

Selected artifacts are touched (`last_referenced_at = NOW()`) so the recency
signal is a real conversation hot-set, not just creation order.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy import select, text as sa_text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings as app_settings
from app.models.artifact import Artifact
from app.providers.factory import get_provider
from app.services.memory.embedder import _resolve_embedding_model

logger = logging.getLogger(__name__)


# How many artifacts we're willing to inline at once. Beyond this the payload
# bloats and the model loses focus.
MAX_GROUNDED_ARTIFACTS = 3

# Cosine similarity floor below which an artifact is considered unrelated.
# similarity = 1 - cosine_distance, so 0.4 keeps reasonably-loose matches.
SIMILARITY_FLOOR = 0.4

# A recent artifact (referenced in the last 5 minutes) is included even with
# zero semantic similarity — the conversation is clearly about it.
RECENT_WINDOW_SECONDS = 300

# Tool-results are transient: they're a snapshot of one ping/snmp/search call
# that the user might want to format/restyle in the next turn — but they
# should NOT be auto-pulled when the user pivots topic ("спасибо", "what's
# the capital of Japan"). So we apply a stricter rule for kind=tool-result:
#   • a tool-result is recent-pulled only inside this much shorter window;
#   • semantic similarity to the new query still works as usual.
TOOL_RESULT_KIND = "tool-result"
# Tool-results are transient: a single ping/snmp snapshot the user might
# want to format in the very next turn. Outside this brief window they must
# earn a slot via semantic similarity like any other artifact.
TOOL_RESULT_VERY_RECENT_SECONDS = 15

# Don't run grounding on trivial queries — search is noisy at short lengths.
MIN_QUERY_CHARS = 5

# Per-artifact content budget when building the system block. Above this the
# content is sliced (head + tail) so 50KB blobs can't blow the payload.
PER_ARTIFACT_MAX_CHARS = 4000

# Total block budget across all grounded artifacts.
TOTAL_BLOCK_BUDGET_CHARS = 10000


async def _embed_query(
    *,
    db: AsyncSession,
    tenant_id: uuid.UUID,
    query: str,
) -> tuple[list[float] | None, str | None]:
    """Compute the embedding for the user query. Returns (vector, model_name)
    or (None, None) if no embedding model is configured."""
    embed_model = await _resolve_embedding_model(tenant_id, db)
    if not embed_model:
        return None, None
    try:
        provider = get_provider(
            "ollama",
            app_settings.OLLAMA_BASE_URL or "http://localhost:11434",
        )
        vectors = await provider.embed(query, embed_model)
        if not vectors:
            return None, embed_model
        return vectors[0], embed_model
    except Exception:
        logger.exception("[grounding] failed to embed query")
        return None, embed_model


def _vec_to_pg(vec: list[float]) -> str:
    """asyncpg doesn't auto-cast list→vector. Serialize to pgvector text form."""
    return "[" + ",".join(f"{float(x):.6f}" for x in vec) + "]"


async def resolve_active_artifacts(
    *,
    db: AsyncSession,
    tenant_id: uuid.UUID,
    chat_id: uuid.UUID,
    user_content: str,
    max_artifacts: int = MAX_GROUNDED_ARTIFACTS,
) -> list[Artifact]:
    """Find artifacts that the LLM should see while answering this user message.

    Strategy: union of
      - top-K by cosine similarity (artifact embedding ↔ query embedding), with
        a similarity floor so unrelated artifacts don't sneak in;
      - everything touched in the last RECENT_WINDOW_SECONDS (the "hot set").
    The result is deduped, capped at max_artifacts, and the chosen artifacts
    have their last_referenced_at bumped to NOW.
    """
    query = (user_content or "").strip()
    # Short queries ("да", "ок", "исправь", "дальше") are *precisely* the ones
    # that lean on context — they have zero semantic signal of their own, but
    # the chat is clearly about whatever the model just produced. We skip the
    # semantic search for them, but the recent hot-set still applies below.
    do_semantic = len(query) >= MIN_QUERY_CHARS

    qvec, embed_model = (
        await _embed_query(db=db, tenant_id=tenant_id, query=query)
        if do_semantic
        else (None, None)
    )

    selected_ids: list[uuid.UUID] = []
    selected_rows: dict[uuid.UUID, Artifact] = {}

    # 1) Semantic top-K (only if we have a query embedding AND there are
    #    artifacts with embeddings to compare against). Filter by current
    #    embedding model so a model-swap backfill in progress doesn't yield
    #    mismatched-dim vectors that crash the cosine-distance op.
    if qvec is not None:
        qvec_str = _vec_to_pg(qvec)
        sql = sa_text(
            """
            SELECT
                id, kind,
                1 - (embedding <=> CAST(:qvec AS vector)) AS similarity
            FROM artifacts
            WHERE tenant_id = :tid
              AND chat_id = :cid
              AND deleted_at IS NULL
              AND embedding IS NOT NULL
              AND embedding_model = :emodel
            ORDER BY embedding <=> CAST(:qvec AS vector)
            LIMIT :k
            """
        )
        rows = (await db.execute(sql, {
            "tid": tenant_id,
            "cid": chat_id,
            "qvec": qvec_str,
            "emodel": embed_model,
            "k": max_artifacts * 2,
        })).fetchall()
        for r in rows:
            if r.similarity is None or r.similarity < SIMILARITY_FLOOR:
                continue
            selected_ids.append(r.id)
            if len(selected_ids) >= max_artifacts:
                break

    # 2) Recent hot-set (last_referenced_at within window).
    #    - Code/PDF/image artifacts: pure recency — chat is clearly about them.
    #    - Tool-result artifacts: ONLY pulled by recency if very-recent
    #      (< TOOL_RESULT_VERY_RECENT_SECONDS, immediate follow-up turn like
    #      "оформи в таблицу"). Outside that window they must earn their slot
    #      via the semantic top-K above (with TOOL_RESULT_SEMANTIC_FLOOR).
    recent_q = sa_text(
        f"""
        SELECT id, kind, last_referenced_at
        FROM artifacts
        WHERE tenant_id = :tid
          AND chat_id = :cid
          AND deleted_at IS NULL
          AND last_referenced_at IS NOT NULL
          AND (
            (kind = :tr_kind  AND last_referenced_at >= NOW() - INTERVAL '{TOOL_RESULT_VERY_RECENT_SECONDS} seconds')
            OR
            (kind != :tr_kind AND last_referenced_at >= NOW() - INTERVAL '{RECENT_WINDOW_SECONDS} seconds')
          )
        ORDER BY last_referenced_at DESC
        LIMIT :k
        """
    )
    recent_rows = (await db.execute(recent_q, {
        "tid": tenant_id, "cid": chat_id, "tr_kind": TOOL_RESULT_KIND, "k": max_artifacts,
    })).fetchall()
    for row in recent_rows:
        if row.id not in selected_ids:
            selected_ids.append(row.id)
            if len(selected_ids) >= max_artifacts:
                break

    # Cap at the per-message budget — semantic hits get priority, recent fills.
    selected_ids = selected_ids[:max_artifacts]
    if not selected_ids:
        return []

    # Fetch the full rows. ORM-loaded — we want access to .content etc.
    rows_q = select(Artifact).where(Artifact.id.in_(selected_ids))
    artifact_rows = (await db.execute(rows_q)).scalars().all()
    by_id = {a.id: a for a in artifact_rows}
    ordered = [by_id[aid] for aid in selected_ids if aid in by_id]

    # Touch last_referenced_at for non-tool-result artifacts only.
    # Tool-results have a fixed lifetime that starts at the tool call — we
    # MUST NOT renew them on retrieval, otherwise grounding keeps a hot
    # ping/snmp result eternally fresh and the model can never escape its
    # "ghost".  Code/script/document artifacts on the other hand should be
    # touched: an actively edited script is "hotter" than a 4-day-old one.
    #
    # Separate session + commit (pipeline session is long-lived; row-locks
    # here would deadlock any tool that touches the same row).
    touch_ids = [a.id for a in ordered if a.kind != TOOL_RESULT_KIND]
    if touch_ids:
        from app.core.database import async_session
        try:
            async with async_session() as touch_db:
                now = datetime.now(timezone.utc)
                await touch_db.execute(
                    update(Artifact)
                    .where(Artifact.id.in_(touch_ids))
                    .values(last_referenced_at=now)
                )
                await touch_db.commit()
        except Exception:
            logger.exception("[grounding] last_referenced_at touch failed (non-fatal)")

    logger.debug(
        "[grounding] resolved %d artifact(s) for chat=%s query='%s...' (embed_model=%s)",
        len(ordered), chat_id, query[:60], embed_model,
    )
    return ordered


def _slice_for_budget(content: str, max_chars: int) -> str:
    """If the artifact is bigger than the slot, keep the head and tail and
    note the elision. Beats a flat head-only cut for code where the bottom
    half (cleanup, returns) is often as relevant as the top."""
    if len(content) <= max_chars:
        return content
    head_chars = int(max_chars * 0.7)
    tail_chars = max_chars - head_chars - 60  # 60 reserved for the elision tag
    head = content[:head_chars]
    tail = content[-tail_chars:] if tail_chars > 0 else ""
    return f"{head}\n\n... [пропущено {len(content) - head_chars - tail_chars} символов] ...\n\n{tail}"


def format_active_artifacts_block(artifacts: list[Artifact]) -> str | None:
    """Render the system-prompt block. Stable order: as given (caller decides)."""
    if not artifacts:
        return None
    parts: list[str] = [
        "## Активные артефакты (источник истины — не пересказ)",
        (
            "Ниже — точное содержимое артефактов, относящихся к вопросу. "
            "На вопросы про их содержимое отвечай ТОЛЬКО по этому блоку, "
            "не по своим резюме / истории. Если ответа здесь нет — так и скажи."
        ),
    ]
    budget_left = TOTAL_BLOCK_BUDGET_CHARS
    for art in artifacts:
        if budget_left <= 0:
            break
        slot = min(PER_ARTIFACT_MAX_CHARS, budget_left)
        body = _slice_for_budget(art.content or "", slot)
        budget_left -= len(body)
        lang_tag = (art.lang or "").strip() or ""
        fence_lang = lang_tag if lang_tag else ""
        header = f"### 📎 [{art.kind}] {art.label}"
        header += f"  (id={art.id}, v{art.version})"
        parts.append(header)
        parts.append(f"```{fence_lang}\n{body}\n```")
    return "\n\n".join(parts)
