"""
Attachment processing pipeline:
1. Extract text from file (PDF, DOCX, XLSX, text, image via OCR, audio via Whisper)
2. Generate summary via LLM
3. Chunk text
4. Generate embeddings for each chunk
"""
import asyncio
import base64
import io
import logging
import uuid

from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.message_attachment import MessageAttachment
from app.models.message_attachment_chunk import MessageAttachmentChunk
from app.models.tenant_shell_config import TenantShellConfig
from app.providers.factory import get_provider
from app.services.storage import read_file
from app.services.kb.embedder import chunk_text
from app.core.config import settings

logger = logging.getLogger(__name__)


# ============================================================
# Content extraction by file type
# ============================================================

def extract_text_content(file_bytes: bytes, filename: str) -> str:
    """Extract text from plain text files."""
    return file_bytes.decode("utf-8", errors="replace")


def extract_pdf_content(file_bytes: bytes) -> str:
    """Extract text from PDF."""
    from pypdf import PdfReader
    reader = PdfReader(io.BytesIO(file_bytes))
    pages = []
    for page in reader.pages:
        text = page.extract_text()
        if text:
            pages.append(text.strip())
    return "\n\n".join(pages)


def extract_docx_content(file_bytes: bytes) -> str:
    """Extract text from DOCX."""
    from docx import Document
    doc = Document(io.BytesIO(file_bytes))
    paragraphs = []
    for para in doc.paragraphs:
        text = para.text.strip()
        if text:
            paragraphs.append(text)
    # Also extract tables
    for table in doc.tables:
        for row in table.rows:
            cells = [cell.text.strip() for cell in row.cells if cell.text.strip()]
            if cells:
                paragraphs.append(" | ".join(cells))
    return "\n\n".join(paragraphs)


def extract_xlsx_content(file_bytes: bytes) -> str:
    """Extract text from XLSX/XLS."""
    from openpyxl import load_workbook
    wb = load_workbook(io.BytesIO(file_bytes), read_only=True, data_only=True)
    parts = []
    for sheet_name in wb.sheetnames:
        ws = wb[sheet_name]
        rows = []
        for row in ws.iter_rows(values_only=True):
            cells = [str(c) if c is not None else "" for c in row]
            if any(c.strip() for c in cells):
                rows.append(" | ".join(cells))
        if rows:
            parts.append(f"[Лист: {sheet_name}]\n" + "\n".join(rows))
    wb.close()
    return "\n\n".join(parts)


def extract_image_ocr(file_bytes: bytes) -> str:
    """Extract text from image using Tesseract OCR."""
    try:
        from PIL import Image
        import pytesseract
        image = Image.open(io.BytesIO(file_bytes))
        text = pytesseract.image_to_string(image, lang="rus+eng")
        return text.strip()
    except Exception as e:
        logger.warning(f"OCR failed: {e}")
        return ""


async def describe_image_vision(file_bytes: bytes, filename: str) -> str:
    """Describe image using Ollama vision model (moondream)."""
    try:
        import httpx
        base_url = (settings.OLLAMA_BASE_URL or "http://localhost:11434").rstrip("/")

        # Check if moondream is available
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{base_url}/api/tags")
            models = [m.get("name", "") for m in resp.json().get("models", [])]
            vision_model = None
            for m in models:
                if any(v in m.lower() for v in ("moondream", "llava", "bakllava", "llama-vision")):
                    vision_model = m
                    break

        if not vision_model:
            logger.info("No vision model available in Ollama, using OCR only")
            return ""

        img_b64 = base64.b64encode(file_bytes).decode("utf-8")

        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(f"{base_url}/api/chat", json={
                "model": vision_model,
                "messages": [{
                    "role": "user",
                    "content": "Подробно опиши что изображено на этой картинке. Если есть текст — перепиши его.",
                    "images": [img_b64],
                }],
                "stream": False,
            })
            resp.raise_for_status()
            data = resp.json()
            return data.get("message", {}).get("content", "").strip()

    except Exception as e:
        logger.warning(f"Vision description failed: {e}")
        return ""


def extract_audio_whisper(file_bytes: bytes, filename: str) -> str:
    """Transcribe audio using Whisper (runs synchronously on CPU/GPU)."""
    import tempfile
    import os

    try:
        import whisper
    except ImportError:
        raise ValueError("Whisper not installed. Install with: pip install openai-whisper")

    # Write to temp file (whisper needs file path)
    suffix = os.path.splitext(filename)[1] or ".wav"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as f:
        f.write(file_bytes)
        tmp_path = f.name

    try:
        model = whisper.load_model("base")
        result = model.transcribe(tmp_path, language=None)
        return result.get("text", "").strip()
    finally:
        os.unlink(tmp_path)


async def extract_content(file_bytes: bytes, filename: str, file_type: str) -> str:
    """Route to the appropriate extractor based on file type."""
    if file_type == "pdf":
        return extract_pdf_content(file_bytes)
    elif file_type == "docx":
        return extract_docx_content(file_bytes)
    elif file_type == "xlsx":
        return extract_xlsx_content(file_bytes)
    elif file_type == "image":
        # Try vision first, then OCR
        vision_text = await describe_image_vision(file_bytes, filename)
        ocr_text = extract_image_ocr(file_bytes)
        parts = []
        if vision_text:
            parts.append(f"[Описание изображения]\n{vision_text}")
        if ocr_text:
            parts.append(f"[Распознанный текст (OCR)]\n{ocr_text}")
        return "\n\n".join(parts) if parts else ""
    elif file_type == "audio":
        # Whisper is CPU-bound — run in thread pool
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, extract_audio_whisper, file_bytes, filename)
    else:
        # text, csv, json, html, xml, md, etc.
        return extract_text_content(file_bytes, filename)


# ============================================================
# Main processing pipeline
# ============================================================

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

        # 2. Extract text based on file type
        content_text = await extract_content(file_bytes, att.filename, att.file_type)
        if not content_text or not content_text.strip():
            raise ValueError(f"No content could be extracted from {att.file_type} file")

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
        from app.services.llm.model_resolver import _resolve_from_shell_config
        resolved = _resolve_from_shell_config(config)

        try:
            summary_text = content_text[:3000]
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

        logger.info(f"Attachment {attachment_id}: {att.file_type} extracted, {len(all_chunks)} chunks embedded")

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
