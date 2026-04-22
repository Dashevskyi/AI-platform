"""
Attachment processing pipeline:
1. Extract text from file (PDF, text, CSV, etc.)
2. Generate summary via LLM
3. Chunk text
4. Generate embeddings for each chunk
"""
import logging
import uuid

from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.message_attachment import MessageAttachment
from app.models.message_attachment_chunk import MessageAttachmentChunk
from app.models.tenant_shell_config import TenantShellConfig
from app.providers.factory import get_provider
from app.services.storage import read_file
from app.services.kb.embedder import chunk_text, extract_file_content
from app.core.config import settings

logger = logging.getLogger(__name__)


async def process_attachment(
    attachment_id: uuid.UUID,
    tenant_id: uuid.UUID,
    db: AsyncSession,
) -> None:
    """
    Process an attachment: extract text, summarize, chunk, embed.
    """
    att = (await db.execute(
        select(MessageAttachment).where(MessageAttachment.id == attachment_id)
    )).scalar_one_or_none()

    if not att:
        logger.error(f"Attachment {attachment_id} not found")
        return

    att.processing_status = "processing"
    att.processing_error = None
    await db.flush()

    try:
        # 1. Read file
        file_bytes = await read_file(att.storage_path)

        # 2. Extract text
        content_text = extract_file_content(file_bytes, att.filename)
        if not content_text or not content_text.strip():
            raise ValueError("No text content could be extracted from file")

        att.content_text = content_text

        # 3. Get embedding config
        config = (await db.execute(
            select(TenantShellConfig).where(TenantShellConfig.tenant_id == tenant_id)
        )).scalar_one_or_none()

        if not config:
            raise ValueError("Shell config not found for tenant")

        embedding_model = config.embedding_model_name
        if not embedding_model:
            raise ValueError("Embedding model not configured")

        # Use Ollama for embeddings (local)
        embed_provider = get_provider("ollama", settings.OLLAMA_BASE_URL or "http://localhost:11434")

        # 4. Generate summary via LLM
        from app.services.llm.model_resolver import resolve_model, _resolve_from_shell_config
        resolved = _resolve_from_shell_config(config)

        try:
            summary_text = content_text[:3000]  # Limit text for summary
            summary_resp = await resolved.provider.chat_completion(
                messages=[{
                    "role": "user",
                    "content": f"Кратко опиши содержимое этого документа (2-3 предложения):\n\n{summary_text}",
                }],
                model=resolved.model_name,
                temperature=0.3,
                max_tokens=200,
            )
            att.summary = summary_resp.content[:500]
        except Exception as e:
            logger.warning(f"Summary generation failed for attachment {attachment_id}: {e}")
            att.summary = f"Файл: {att.filename} ({att.file_type}, {att.file_size_bytes} байт)"

        # 5. Chunk text
        chunks = chunk_text(content_text)
        if not chunks:
            raise ValueError("No chunks generated from content")

        # 6. Delete old chunks
        await db.execute(
            delete(MessageAttachmentChunk).where(MessageAttachmentChunk.attachment_id == attachment_id)
        )

        # 7. Generate embeddings in batches
        BATCH_SIZE = 10
        all_chunks = []

        for i in range(0, len(chunks), BATCH_SIZE):
            batch = chunks[i:i + BATCH_SIZE]
            embeddings = await embed_provider.embed(batch, embedding_model)

            for j, (chunk_text_item, emb) in enumerate(zip(batch, embeddings)):
                chunk = MessageAttachmentChunk(
                    attachment_id=att.id,
                    tenant_id=tenant_id,
                    chunk_index=i + j,
                    content=chunk_text_item,
                    embedding=emb,
                )
                db.add(chunk)
                all_chunks.append(chunk)

        att.chunks_count = len(all_chunks)
        att.processing_status = "done"
        att.processing_error = None
        await db.flush()

        logger.info(f"Attachment {attachment_id}: extracted, {len(all_chunks)} chunks embedded")

    except Exception as e:
        att.processing_status = "error"
        att.processing_error = str(e)[:500]
        await db.flush()
        logger.error(f"Attachment {attachment_id} processing failed: {e}")


async def search_attachment_chunks(
    attachment_id: str,
    query: str,
    db: AsyncSession,
    provider,
    embedding_model: str,
    max_results: int = 5,
) -> list[MessageAttachmentChunk]:
    """
    Semantic search within a specific attachment's chunks.
    """
    query_embeddings = await provider.embed(query, embedding_model)
    query_vector = query_embeddings[0]

    stmt = (
        select(MessageAttachmentChunk)
        .where(
            MessageAttachmentChunk.attachment_id == attachment_id,
            MessageAttachmentChunk.embedding.isnot(None),
        )
        .order_by(MessageAttachmentChunk.embedding.cosine_distance(query_vector))
        .limit(max_results)
    )

    result = await db.execute(stmt)
    return list(result.scalars().all())
