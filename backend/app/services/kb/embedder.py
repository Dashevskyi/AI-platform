"""
KB document processing: chunking, embedding, URL scraping, file parsing.
"""
import logging
import re
import uuid

import httpx
from bs4 import BeautifulSoup
from pypdf import PdfReader
from io import BytesIO
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.kb_document import KnowledgeBaseDocument
from app.models.kb_chunk import KBChunk
from app.models.tenant_shell_config import TenantShellConfig
from app.providers.factory import get_provider
from app.core.security import decrypt_value

logger = logging.getLogger(__name__)

# --- Text chunking ---

CHUNK_SIZE = 800       # characters per chunk
CHUNK_OVERLAP = 100    # overlap between chunks


def chunk_text(text: str, chunk_size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    """Split text into overlapping chunks by paragraphs/sentences."""
    text = text.strip()
    if not text:
        return []

    # Split by double newlines (paragraphs) first
    paragraphs = re.split(r'\n{2,}', text)

    chunks: list[str] = []
    current = ""

    for para in paragraphs:
        para = para.strip()
        if not para:
            continue

        if len(current) + len(para) + 2 <= chunk_size:
            current = f"{current}\n\n{para}" if current else para
        else:
            if current:
                chunks.append(current.strip())
            # If paragraph itself is too long, split by sentences
            if len(para) > chunk_size:
                sentences = re.split(r'(?<=[.!?。])\s+', para)
                current = ""
                for sent in sentences:
                    if len(current) + len(sent) + 1 <= chunk_size:
                        current = f"{current} {sent}" if current else sent
                    else:
                        if current:
                            chunks.append(current.strip())
                        current = sent
            else:
                current = para

    if current.strip():
        chunks.append(current.strip())

    # Apply overlap: prepend tail of previous chunk
    if overlap > 0 and len(chunks) > 1:
        overlapped = [chunks[0]]
        for i in range(1, len(chunks)):
            prev_tail = chunks[i - 1][-overlap:]
            # Find word boundary in overlap
            space_idx = prev_tail.find(' ')
            if space_idx > 0:
                prev_tail = prev_tail[space_idx + 1:]
            overlapped.append(f"...{prev_tail} {chunks[i]}")
        chunks = overlapped

    return chunks


# --- Content extraction ---

async def fetch_url_content(url: str) -> str:
    """Fetch and extract text content from a URL."""
    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        response = await client.get(url, headers={
            "User-Agent": "Mozilla/5.0 (compatible; KBBot/1.0)",
        })
        response.raise_for_status()
        html = response.text

    soup = BeautifulSoup(html, "html.parser")

    # Remove scripts, styles, nav, footer
    for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
        tag.decompose()

    # Try to find main content
    main = soup.find("main") or soup.find("article") or soup.find("body")
    if main:
        text = main.get_text(separator="\n", strip=True)
    else:
        text = soup.get_text(separator="\n", strip=True)

    # Clean up excessive whitespace
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return "\n\n".join(lines)


def extract_pdf_content(file_bytes: bytes) -> str:
    """Extract text from a PDF file."""
    reader = PdfReader(BytesIO(file_bytes))
    pages = []
    for page in reader.pages:
        text = page.extract_text()
        if text:
            pages.append(text.strip())
    return "\n\n".join(pages)


def extract_file_content(file_bytes: bytes, filename: str) -> str:
    """Extract text content from an uploaded file."""
    lower = filename.lower()
    if lower.endswith(".pdf"):
        return extract_pdf_content(file_bytes)
    elif lower.endswith((".txt", ".md", ".csv", ".log", ".json", ".xml", ".html")):
        return file_bytes.decode("utf-8", errors="replace")
    else:
        # Try as text
        return file_bytes.decode("utf-8", errors="replace")


# --- Embedding pipeline ---

async def process_document(doc_id: uuid.UUID, tenant_id: uuid.UUID, db: AsyncSession) -> None:
    """
    Full processing pipeline for a KB document:
    1. Load document
    2. Chunk content
    3. Generate embeddings via provider
    4. Save chunks with embeddings
    """
    doc = (await db.execute(
        select(KnowledgeBaseDocument).where(KnowledgeBaseDocument.id == doc_id)
    )).scalar_one_or_none()

    if not doc:
        logger.error(f"KB document {doc_id} not found")
        return

    # Update status
    doc.embedding_status = "processing"
    doc.embedding_error = None
    await db.flush()

    try:
        # Get shell config for embedding model
        config = (await db.execute(
            select(TenantShellConfig).where(TenantShellConfig.tenant_id == tenant_id)
        )).scalar_one_or_none()

        if not config:
            raise ValueError("Shell config not found for tenant")

        embedding_model = config.embedding_model_name
        if not embedding_model:
            raise ValueError("Embedding model not configured. Set embedding_model_name in shell config.")

        # Init provider for embeddings — use Ollama (local) since embedding models run locally
        from app.core.config import settings
        provider = get_provider("ollama", settings.OLLAMA_BASE_URL or "http://localhost:11434")

        # Chunk content
        chunks = chunk_text(doc.content)
        if not chunks:
            raise ValueError("Document has no content to chunk")

        # Delete old chunks
        await db.execute(
            delete(KBChunk).where(KBChunk.document_id == doc_id)
        )

        # Generate embeddings in batches
        BATCH_SIZE = 10
        all_chunk_objs = []

        for i in range(0, len(chunks), BATCH_SIZE):
            batch = chunks[i:i + BATCH_SIZE]
            embeddings = await provider.embed(batch, embedding_model)

            for j, (chunk_text_item, emb) in enumerate(zip(batch, embeddings)):
                chunk = KBChunk(
                    document_id=doc.id,
                    tenant_id=tenant_id,
                    chunk_index=i + j,
                    content=chunk_text_item,
                    doc_title=doc.title,
                    source_type=doc.source_type,
                    source_url=doc.source_url,
                    embedding=emb,
                )
                db.add(chunk)
                all_chunk_objs.append(chunk)

        doc.chunks_count = len(all_chunk_objs)
        doc.embedding_status = "done"
        doc.embedding_error = None
        await db.flush()

        logger.info(f"KB document {doc_id}: {len(all_chunk_objs)} chunks embedded")

    except Exception as e:
        doc.embedding_status = "error"
        doc.embedding_error = str(e)[:500]
        await db.flush()
        logger.error(f"KB document {doc_id} embedding failed: {e}")


async def search_kb_chunks(
    tenant_id: str,
    query: str,
    db: AsyncSession,
    provider,
    embedding_model: str,
    max_results: int = 10,
) -> list[KBChunk]:
    """
    Semantic search: embed query, find closest chunks via cosine distance.
    """
    # Embed query
    query_embeddings = await provider.embed(query, embedding_model)
    query_vector = query_embeddings[0]

    # Cosine distance search using pgvector
    stmt = (
        select(KBChunk)
        .where(
            KBChunk.tenant_id == tenant_id,
            KBChunk.embedding.isnot(None),
        )
        .join(
            KnowledgeBaseDocument,
            KBChunk.document_id == KnowledgeBaseDocument.id,
        )
        .where(
            KnowledgeBaseDocument.is_active == True,  # noqa: E712
            KnowledgeBaseDocument.deleted_at.is_(None),
        )
        .order_by(KBChunk.embedding.cosine_distance(query_vector))
        .limit(max_results)
    )

    result = await db.execute(stmt)
    return list(result.scalars().all())
