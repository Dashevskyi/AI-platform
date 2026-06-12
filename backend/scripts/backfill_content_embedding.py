"""One-off backfill: populate messages.content_embedding for existing pairs.

For every user message that has a resume_embedding but no content_embedding,
embed the raw trimmed Q+A and store it — so factual recall_chat works on
historical conversations, not just new ones.

Run: cd /home/ai-platform/backend && ./venv/bin/python scripts/backfill_content_embedding.py [tenant_id]
"""
import asyncio
import sys

from sqlalchemy import select, text

from app.core.database import async_session
from app.core.config import settings as app_settings
from app.models.message import Message
from app.providers.factory import get_provider
from app.services.memory.embedder import _resolve_embedding_model

RAW_EMBED_CAP = 2000


async def main() -> None:
    tenant_filter = sys.argv[1] if len(sys.argv) > 1 else None
    provider = get_provider("ollama", app_settings.OLLAMA_BASE_URL or "http://localhost:11434")

    async with async_session() as db:
        q = (
            select(Message.id, Message.tenant_id, Message.chat_id, Message.content, Message.created_at)
            .where(
                Message.role == "user",
                Message.resume_embedding.isnot(None),
                Message.content_embedding.is_(None),
            )
            .order_by(Message.created_at.asc())
        )
        if tenant_filter:
            import uuid as _uuid
            q = q.where(Message.tenant_id == _uuid.UUID(tenant_filter))
        rows = (await db.execute(q)).all()

    print(f"pairs to backfill: {len(rows)}")
    done = 0
    model_cache: dict[str, str | None] = {}

    for r in rows:
        async with async_session() as db:
            tid = str(r.tenant_id)
            if tid not in model_cache:
                model_cache[tid] = await _resolve_embedding_model(tid, db)
            embed_model = model_cache[tid]
            if not embed_model:
                continue

            # matching assistant reply (first after the user msg, same chat)
            asst = (await db.execute(
                select(Message.content).where(
                    Message.chat_id == r.chat_id,
                    Message.role == "assistant",
                    Message.created_at >= r.created_at,
                ).order_by(Message.created_at.asc()).limit(1)
            )).scalar_one_or_none()

            raw = f"{(r.content or '')[:RAW_EMBED_CAP]}\n{(asst or '')[:RAW_EMBED_CAP]}".strip()
            if not raw:
                continue
            try:
                vectors = await provider.embed(raw, embed_model)
            except Exception as e:
                print(f"  embed failed for {r.id}: {e}")
                continue
            if not vectors:
                continue
            await db.execute(
                text("UPDATE messages SET content_embedding = :v WHERE id = :id"),
                {"v": str(vectors[0]), "id": str(r.id)},
            )
            await db.commit()
            done += 1
            if done % 25 == 0:
                print(f"  {done}/{len(rows)}")

    print(f"backfilled {done} pairs")


if __name__ == "__main__":
    asyncio.run(main())
