"""
Memory entry embeddings + semantic search (mirrors KB embedder, simpler:
one entry = one embedding, no chunking).
"""
import logging
import uuid
from typing import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings as app_settings
from app.core.database import async_session
from app.models.memory_entry import MemoryEntry
from app.models.tenant_shell_config import TenantShellConfig
from app.providers.factory import get_provider

logger = logging.getLogger(__name__)


async def _resolve_embedding_model(tenant_id: uuid.UUID, db: AsyncSession) -> str | None:
    cfg = (
        await db.execute(
            select(TenantShellConfig).where(TenantShellConfig.tenant_id == tenant_id)
        )
    ).scalar_one_or_none()
    return (cfg.embedding_model_name if cfg else None) or None


async def embed_memory_entry(memory_id: uuid.UUID) -> None:
    """Compute and store embedding for a single memory entry. Uses Ollama."""
    async with async_session() as db:
        entry = (
            await db.execute(select(MemoryEntry).where(MemoryEntry.id == memory_id))
        ).scalar_one_or_none()
        if not entry or entry.deleted_at is not None:
            return
        if not (entry.content or "").strip():
            return

        model = await _resolve_embedding_model(entry.tenant_id, db)
        if not model:
            logger.debug("memory embed: no embedding_model configured for tenant=%s, skip", entry.tenant_id)
            return

        try:
            provider = get_provider("ollama", app_settings.OLLAMA_BASE_URL or "http://localhost:11434")
            vectors = await provider.embed(entry.content, model)
            if not vectors:
                return
            entry.embedding = vectors[0]
            entry.embedding_model = model
            await db.commit()
        except Exception:
            logger.exception("memory embed failed for id=%s", memory_id)
            await db.rollback()


async def embed_pending_for_tenant(tenant_id: uuid.UUID, batch_size: int = 50) -> int:
    """Backfill: embed all entries that don't yet have an embedding."""
    embedded = 0
    async with async_session() as db:
        model = await _resolve_embedding_model(tenant_id, db)
        if not model:
            return 0
        provider = get_provider("ollama", app_settings.OLLAMA_BASE_URL or "http://localhost:11434")

        while True:
            rows = (
                await db.execute(
                    select(MemoryEntry).where(
                        MemoryEntry.tenant_id == tenant_id,
                        MemoryEntry.deleted_at.is_(None),
                        MemoryEntry.embedding.is_(None),
                    ).limit(batch_size)
                )
            ).scalars().all()
            if not rows:
                break
            texts = [r.content for r in rows]
            try:
                vectors = await provider.embed(texts, model)
            except Exception:
                logger.exception("memory backfill batch failed for tenant=%s", tenant_id)
                break
            for entry, vec in zip(rows, vectors):
                entry.embedding = vec
                entry.embedding_model = model
                embedded += 1
            await db.commit()
    return embedded


async def search_memory_entries(
    *,
    tenant_id: str,
    chat_id: str | None,
    query: str,
    db: AsyncSession,
    embedding_model: str | None,
    top_k: int = 8,
) -> Sequence[MemoryEntry]:
    """
    Semantic search across tenant memory.
    Returns top_k entries by cosine distance to `query` embedding.
    Pinned entries are NOT included here — caller adds them separately.
    """
    if not query or not query.strip() or not embedding_model:
        return []
    try:
        provider = get_provider("ollama", app_settings.OLLAMA_BASE_URL or "http://localhost:11434")
        vectors = await provider.embed(query, embedding_model)
    except Exception:
        logger.exception("memory query embed failed")
        return []
    if not vectors:
        return []
    qv = vectors[0]

    stmt = (
        select(MemoryEntry)
        .where(
            MemoryEntry.tenant_id == uuid.UUID(str(tenant_id)),
            MemoryEntry.deleted_at.is_(None),
            MemoryEntry.embedding.isnot(None),
            MemoryEntry.is_pinned.is_(False),  # pinned go via separate path
            (MemoryEntry.chat_id == uuid.UUID(str(chat_id))) if chat_id else (MemoryEntry.chat_id.is_(None)) | (MemoryEntry.chat_id.is_(None)),
        )
        .order_by(MemoryEntry.embedding.cosine_distance(qv))
        .limit(top_k)
    )
    # Note: chat scoping — include both chat-specific and tenant-wide (chat_id IS NULL)
    if chat_id:
        stmt = (
            select(MemoryEntry)
            .where(
                MemoryEntry.tenant_id == uuid.UUID(str(tenant_id)),
                MemoryEntry.deleted_at.is_(None),
                MemoryEntry.embedding.isnot(None),
                MemoryEntry.is_pinned.is_(False),
                (MemoryEntry.chat_id == uuid.UUID(str(chat_id))) | (MemoryEntry.chat_id.is_(None)),
            )
            .order_by(MemoryEntry.embedding.cosine_distance(qv))
            .limit(top_k)
        )

    return list((await db.execute(stmt)).scalars().all())
