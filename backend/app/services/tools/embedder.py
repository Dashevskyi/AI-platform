"""
Tool embeddings + semantic search.

Each tool is embedded as a single vector built from name + description +
capability_tags. Used by pipeline._select_relevant_tools when tenant has
many tools (default >80) so we don't blow the LLM context.
"""
import logging
import uuid
from typing import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings as app_settings
from app.core.database import async_session
from app.models.tenant_tool import TenantTool
from app.models.tenant_shell_config import TenantShellConfig
from app.providers.factory import get_provider

logger = logging.getLogger(__name__)


def _tool_to_text(tool: TenantTool) -> str:
    """Build the text we embed for a tool. Includes:
    - name
    - description (from function.description, falls back to tool.description)
    - parameter descriptions (covers query/property docs which often describe usage)
    - capability_tags
    - usage_examples — list of natural-language queries that should match this tool
    - aliases — alternative names users might use
    - group
    The richer this text is, the more robust semantic top-K becomes."""
    parts: list[str] = []
    if tool.name:
        parts.append(f"Tool: {tool.name}")

    cfg = tool.config_json or {}
    runtime = cfg.get("x_backend_config") or {}
    fn = (cfg.get("function") or {}) if isinstance(cfg.get("function"), dict) else {}

    # description: prefer fn.description over tool.description (admin UI keeps richer text there)
    desc = (fn.get("description") or "").strip() or (tool.description or "").strip()
    if desc:
        parts.append(desc)

    # Parameter descriptions — often contain the actual user-facing wording
    props = (fn.get("parameters") or {}).get("properties") or {}
    param_desc_chunks: list[str] = []
    if isinstance(props, dict):
        for pname, pdef in props.items():
            if isinstance(pdef, dict):
                pdesc = (pdef.get("description") or "").strip()
                if pdesc:
                    param_desc_chunks.append(f"{pname}: {pdesc}")
                inner = pdef.get("properties")
                if isinstance(inner, dict):
                    for sub, sdef in inner.items():
                        if isinstance(sdef, dict):
                            sdesc = (sdef.get("description") or "").strip()
                            if sdesc:
                                param_desc_chunks.append(f"{pname}.{sub}: {sdesc}")
    if param_desc_chunks:
        parts.append("Параметры: " + "; ".join(param_desc_chunks[:12]))

    tags = runtime.get("capability_tags") or []
    if isinstance(tags, list):
        flat_tags = [str(t).strip() for t in tags if t and isinstance(t, str)]
        if flat_tags:
            parts.append("Теги: " + ", ".join(flat_tags))

    # usage_examples — array of natural-language phrases that should match this tool
    examples = runtime.get("usage_examples") or []
    if isinstance(examples, list):
        flat_ex = [str(e).strip() for e in examples if e and isinstance(e, str)]
        if flat_ex:
            parts.append("Когда вызывать: " + " | ".join(flat_ex))

    aliases = runtime.get("aliases") or []
    if isinstance(aliases, list):
        flat_al = [str(a).strip() for a in aliases if a and isinstance(a, str)]
        if flat_al:
            parts.append("Синонимы: " + ", ".join(flat_al))

    if tool.group:
        parts.append(f"Категория: {tool.group}")
    return "\n".join(parts).strip()


async def _resolve_embedding_model(tenant_id: uuid.UUID, db: AsyncSession) -> str | None:
    cfg = (
        await db.execute(
            select(TenantShellConfig).where(TenantShellConfig.tenant_id == tenant_id)
        )
    ).scalar_one_or_none()
    return (cfg.embedding_model_name if cfg else None) or None


async def embed_tool(tool_id: uuid.UUID) -> None:
    """Compute and store embedding for a single tool."""
    async with async_session() as db:
        tool = (
            await db.execute(select(TenantTool).where(TenantTool.id == tool_id))
        ).scalar_one_or_none()
        if not tool or tool.deleted_at is not None or not tool.is_active:
            return
        text = _tool_to_text(tool)
        if not text:
            return
        model = await _resolve_embedding_model(tool.tenant_id, db)
        if not model:
            logger.debug("tool embed: no embedding_model configured, skip tool=%s", tool_id)
            return
        try:
            provider = get_provider("ollama", app_settings.OLLAMA_BASE_URL or "http://localhost:11434")
            vectors = await provider.embed(text, model)
            if not vectors:
                return
            tool.embedding = vectors[0]
            tool.embedding_model = model
            await db.commit()
        except Exception:
            logger.exception("tool embed failed for id=%s", tool_id)
            await db.rollback()


async def embed_pending_for_tenant(tenant_id: uuid.UUID, batch_size: int = 25) -> int:
    """Backfill: embed all active tools without an embedding."""
    embedded = 0
    async with async_session() as db:
        model = await _resolve_embedding_model(tenant_id, db)
        if not model:
            return 0
        provider = get_provider("ollama", app_settings.OLLAMA_BASE_URL or "http://localhost:11434")
        while True:
            rows = (
                await db.execute(
                    select(TenantTool).where(
                        TenantTool.tenant_id == tenant_id,
                        TenantTool.deleted_at.is_(None),
                        TenantTool.is_active.is_(True),
                        TenantTool.embedding.is_(None),
                    ).limit(batch_size)
                )
            ).scalars().all()
            if not rows:
                break
            texts = [_tool_to_text(r) for r in rows]
            try:
                vectors = await provider.embed(texts, model)
            except Exception:
                logger.exception("tool backfill batch failed for tenant=%s", tenant_id)
                break
            for tool, vec in zip(rows, vectors):
                tool.embedding = vec
                tool.embedding_model = model
                embedded += 1
            await db.commit()
    return embedded


async def search_tools(
    *,
    tenant_id: str,
    query: str,
    db: AsyncSession,
    embedding_model: str | None,
    candidate_ids: Sequence[uuid.UUID] | None = None,
    top_k: int = 25,
) -> list[TenantTool]:
    """
    Semantic search over active tools. Returns top_k by cosine distance to `query`.
    `candidate_ids` optionally restricts the search (after group / API-key filter).
    Pinned tools are NOT included here — caller adds them separately.
    """
    if not query or not query.strip() or not embedding_model:
        return []
    try:
        provider = get_provider("ollama", app_settings.OLLAMA_BASE_URL or "http://localhost:11434")
        vectors = await provider.embed(query, embedding_model)
    except Exception:
        logger.exception("tool query embed failed")
        return []
    if not vectors:
        return []
    qv = vectors[0]

    stmt = (
        select(TenantTool)
        .where(
            TenantTool.tenant_id == uuid.UUID(str(tenant_id)),
            TenantTool.deleted_at.is_(None),
            TenantTool.is_active.is_(True),
            TenantTool.embedding.isnot(None),
            TenantTool.is_pinned.is_(False),
        )
        .order_by(TenantTool.embedding.cosine_distance(qv))
        .limit(top_k)
    )
    if candidate_ids is not None:
        stmt = stmt.where(TenantTool.id.in_(list(candidate_ids)))

    return list((await db.execute(stmt)).scalars().all())
