"""
Admin CRUD for tenant knowledge base documents.
Supports text, URL, and file upload sources with vector embeddings.
"""
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File, Form, status
from pydantic import BaseModel
from sqlalchemy import select, func, delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.models.admin_user import AdminUser
from app.models.tenant import Tenant
from app.models.tenant_shell_config import TenantShellConfig
from app.models.kb_document import KnowledgeBaseDocument
from app.models.kb_chunk import KBChunk
from app.providers.factory import get_provider
from app.schemas.kb import KBDocumentCreate, KBDocumentUpdate, KBDocumentResponse
from app.schemas.common import PaginatedResponse
from app.api.deps import require_role, require_tenant_access, require_permission
from app.services.kb.embedder import (
    fetch_url_content,
    extract_file_content,
    process_document,
)

router = APIRouter(
    prefix="/api/admin/tenants/{tenant_id}/kb",
    tags=["admin-kb"],
    dependencies=[Depends(require_role("superadmin", "tenant_admin")), Depends(require_tenant_access), Depends(require_permission("kb"))],
)

SOURCE_TYPES = ("manual", "faq", "solution", "procedure", "reference")
DOC_TYPES = ("text", "url", "file")


def _doc_to_response(d: KnowledgeBaseDocument) -> KBDocumentResponse:
    return KBDocumentResponse(
        id=str(d.id),
        tenant_id=str(d.tenant_id),
        title=d.title,
        doc_type=d.doc_type,
        source_type=d.source_type,
        source_url=d.source_url,
        source_filename=d.source_filename,
        content=d.content,
        metadata_json=d.metadata_json,
        is_active=d.is_active,
        embedding_status=d.embedding_status,
        embedding_error=d.embedding_error,
        chunks_count=d.chunks_count,
        created_at=d.created_at,
        updated_at=d.updated_at,
    )


async def _verify_tenant(tenant_id: uuid.UUID, db: AsyncSession) -> Tenant:
    result = await db.execute(
        select(Tenant).where(Tenant.id == tenant_id, Tenant.deleted_at.is_(None))
    )
    tenant = result.scalars().first()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found.")
    return tenant


@router.get("/", response_model=PaginatedResponse[KBDocumentResponse])
async def list_documents(
    tenant_id: uuid.UUID,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    doc_type: str | None = Query(None),
    source_type: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
):
    await _verify_tenant(tenant_id, db)

    query = (
        select(KnowledgeBaseDocument)
        .where(
            KnowledgeBaseDocument.tenant_id == tenant_id,
            KnowledgeBaseDocument.deleted_at.is_(None),
        )
    )

    if doc_type:
        query = query.where(KnowledgeBaseDocument.doc_type == doc_type)
    if source_type:
        query = query.where(KnowledgeBaseDocument.source_type == source_type)

    query = query.order_by(KnowledgeBaseDocument.created_at.desc())

    count_q = select(func.count()).select_from(query.subquery())
    total = (await db.execute(count_q)).scalar()

    items = (
        await db.execute(query.offset((page - 1) * page_size).limit(page_size))
    ).scalars().all()

    return PaginatedResponse[KBDocumentResponse](
        items=[_doc_to_response(d) for d in items],
        total_count=total,
        page=page,
        page_size=page_size,
    )


@router.post("/", response_model=KBDocumentResponse, status_code=status.HTTP_201_CREATED)
async def create_document(
    tenant_id: uuid.UUID,
    body: KBDocumentCreate,
    db: AsyncSession = Depends(get_db),
):
    """Create a KB document from text or URL. Content is auto-chunked and embedded."""
    await _verify_tenant(tenant_id, db)

    content = body.content

    # If URL type, fetch content from URL
    if body.doc_type == "url":
        if not body.source_url:
            raise HTTPException(status_code=400, detail="source_url is required for URL documents")
        try:
            content = await fetch_url_content(body.source_url)
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Failed to fetch URL: {e}")
        if not content.strip():
            raise HTTPException(status_code=400, detail="No content extracted from URL")

    if not content.strip():
        raise HTTPException(status_code=400, detail="Content is required")

    doc = KnowledgeBaseDocument(
        tenant_id=tenant_id,
        title=body.title,
        doc_type=body.doc_type,
        source_type=body.source_type,
        source_url=body.source_url,
        content=content,
        metadata_json=body.metadata_json,
        is_active=body.is_active,
        embedding_status="pending",
    )
    db.add(doc)
    await db.flush()
    await db.refresh(doc)

    # Process embeddings inline
    try:
        await process_document(doc.id, tenant_id, db)
    except Exception as e:
        doc.embedding_status = "error"
        doc.embedding_error = str(e)[:500]

    await db.refresh(doc)
    return _doc_to_response(doc)


@router.post("/upload", response_model=KBDocumentResponse, status_code=status.HTTP_201_CREATED)
async def upload_document(
    tenant_id: uuid.UUID,
    file: UploadFile = File(...),
    title: str = Form(...),
    source_type: str = Form("manual"),
    db: AsyncSession = Depends(get_db),
):
    """Upload a file (PDF, TXT, MD, etc.) as a KB document."""
    await _verify_tenant(tenant_id, db)

    file_bytes = await file.read()
    if len(file_bytes) > 10 * 1024 * 1024:  # 10MB limit
        raise HTTPException(status_code=400, detail="File too large (max 10MB)")

    filename = file.filename or "uploaded_file"

    try:
        content = extract_file_content(file_bytes, filename)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to extract content: {e}")

    if not content.strip():
        raise HTTPException(status_code=400, detail="No text content extracted from file")

    doc = KnowledgeBaseDocument(
        tenant_id=tenant_id,
        title=title,
        doc_type="file",
        source_type=source_type,
        source_filename=filename,
        content=content,
        embedding_status="pending",
    )
    db.add(doc)
    await db.flush()
    await db.refresh(doc)

    # Process embeddings
    try:
        await process_document(doc.id, tenant_id, db)
    except Exception as e:
        doc.embedding_status = "error"
        doc.embedding_error = str(e)[:500]

    await db.refresh(doc)
    return _doc_to_response(doc)


class KBChunkRow(BaseModel):
    chunk_index: int
    chars: int
    has_embedding: bool
    content: str


@router.get("/{doc_id}/chunks", response_model=list[KBChunkRow])
async def list_document_chunks(
    tenant_id: uuid.UUID,
    doc_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """How a document was split for retrieval — its chunks in order, with size
    and whether each got an embedding."""
    await _verify_tenant(tenant_id, db)
    rows = (await db.execute(
        select(KBChunk)
        .where(KBChunk.tenant_id == tenant_id, KBChunk.document_id == doc_id)
        .order_by(KBChunk.chunk_index)
    )).scalars().all()
    return [
        KBChunkRow(
            chunk_index=c.chunk_index,
            chars=len(c.content or ""),
            has_embedding=c.embedding is not None,
            content=c.content or "",
        )
        for c in rows
    ]


class KBSearchPreviewRequest(BaseModel):
    query: str
    limit: int = 8


class KBPreviewChunk(BaseModel):
    chunk_id: str
    document_id: str
    doc_title: str | None
    chunk_index: int | None
    content: str
    relevance: float  # 1 - cosine distance (higher = closer)


@router.post("/search-preview", response_model=list[KBPreviewChunk])
async def search_preview(
    tenant_id: uuid.UUID,
    body: KBSearchPreviewRequest,
    db: AsyncSession = Depends(get_db),
):
    """Preview semantic retrieval: embed the query and return the top chunks with
    a relevance score, exactly as the chat pipeline would rank them."""
    await _verify_tenant(tenant_id, db)
    query = (body.query or "").strip()
    if not query:
        raise HTTPException(status_code=400, detail="Пустой запрос")
    config = (await db.execute(
        select(TenantShellConfig).where(TenantShellConfig.tenant_id == tenant_id)
    )).scalar_one_or_none()
    model = getattr(config, "embedding_model_name", None) if config else None
    if not model:
        raise HTTPException(status_code=400, detail="Embedding-модель не настроена в shell config.")
    provider = get_provider("ollama", settings.OLLAMA_BASE_URL or "http://localhost:11434")
    try:
        embs = await provider.embed(query, model)
        qvec = embs[0]
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Не удалось получить эмбеддинг запроса: {str(e)[:200]}")

    dist = KBChunk.embedding.cosine_distance(qvec).label("distance")
    stmt = (
        select(KBChunk, dist)
        .join(KnowledgeBaseDocument, KBChunk.document_id == KnowledgeBaseDocument.id)
        .where(
            KBChunk.tenant_id == tenant_id,
            KBChunk.embedding.isnot(None),
            KnowledgeBaseDocument.is_active == True,  # noqa: E712
            KnowledgeBaseDocument.deleted_at.is_(None),
        )
        .order_by(dist)
        .limit(max(1, min(body.limit, 20)))
    )
    rows = (await db.execute(stmt)).all()
    return [
        KBPreviewChunk(
            chunk_id=str(c.id),
            document_id=str(c.document_id),
            doc_title=getattr(c, "doc_title", None),
            chunk_index=getattr(c, "chunk_index", None),
            content=(c.content or "")[:1000],
            relevance=max(0.0, 1.0 - float(d)),
        )
        for c, d in rows
    ]


@router.get("/{doc_id}", response_model=KBDocumentResponse)
async def get_document(
    tenant_id: uuid.UUID,
    doc_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    await _verify_tenant(tenant_id, db)

    result = await db.execute(
        select(KnowledgeBaseDocument).where(
            KnowledgeBaseDocument.id == doc_id,
            KnowledgeBaseDocument.tenant_id == tenant_id,
            KnowledgeBaseDocument.deleted_at.is_(None),
        )
    )
    doc = result.scalars().first()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found.")
    return _doc_to_response(doc)


@router.patch("/{doc_id}", response_model=KBDocumentResponse)
async def update_document(
    tenant_id: uuid.UUID,
    doc_id: uuid.UUID,
    body: KBDocumentUpdate,
    db: AsyncSession = Depends(get_db),
):
    await _verify_tenant(tenant_id, db)

    result = await db.execute(
        select(KnowledgeBaseDocument).where(
            KnowledgeBaseDocument.id == doc_id,
            KnowledgeBaseDocument.tenant_id == tenant_id,
            KnowledgeBaseDocument.deleted_at.is_(None),
        )
    )
    doc = result.scalars().first()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found.")

    updates = body.model_dump(exclude_unset=True)
    content_changed = "content" in updates and updates["content"] != doc.content
    title_changed = "title" in updates and updates["title"] != doc.title

    for field, value in updates.items():
        setattr(doc, field, value)

    await db.flush()

    # Re-embed if content or title changed
    if content_changed or title_changed:
        try:
            await process_document(doc.id, tenant_id, db)
        except Exception as e:
            doc.embedding_status = "error"
            doc.embedding_error = str(e)[:500]

    await db.refresh(doc)
    return _doc_to_response(doc)


@router.post("/{doc_id}/reembed", response_model=KBDocumentResponse)
async def reembed_document(
    tenant_id: uuid.UUID,
    doc_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """Force re-embedding of a document (e.g. after changing embedding model)."""
    await _verify_tenant(tenant_id, db)

    result = await db.execute(
        select(KnowledgeBaseDocument).where(
            KnowledgeBaseDocument.id == doc_id,
            KnowledgeBaseDocument.tenant_id == tenant_id,
            KnowledgeBaseDocument.deleted_at.is_(None),
        )
    )
    doc = result.scalars().first()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found.")

    try:
        await process_document(doc.id, tenant_id, db)
    except Exception as e:
        doc.embedding_status = "error"
        doc.embedding_error = str(e)[:500]

    await db.refresh(doc)
    return _doc_to_response(doc)


@router.post("/reembed-all", status_code=status.HTTP_200_OK)
async def reembed_all_documents(
    tenant_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """Re-embed all active KB documents for the tenant."""
    await _verify_tenant(tenant_id, db)

    docs = (await db.execute(
        select(KnowledgeBaseDocument).where(
            KnowledgeBaseDocument.tenant_id == tenant_id,
            KnowledgeBaseDocument.is_active == True,  # noqa: E712
            KnowledgeBaseDocument.deleted_at.is_(None),
        )
    )).scalars().all()

    results = {"total": len(docs), "success": 0, "error": 0}
    for doc in docs:
        try:
            await process_document(doc.id, tenant_id, db)
            results["success"] += 1
        except Exception:
            results["error"] += 1

    return results


@router.delete("/{doc_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(
    tenant_id: uuid.UUID,
    doc_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: AdminUser = Depends(require_role("superadmin", "tenant_admin")),
):
    await _verify_tenant(tenant_id, db)

    result = await db.execute(
        select(KnowledgeBaseDocument).where(
            KnowledgeBaseDocument.id == doc_id,
            KnowledgeBaseDocument.tenant_id == tenant_id,
            KnowledgeBaseDocument.deleted_at.is_(None),
        )
    )
    doc = result.scalars().first()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found.")

    # Delete chunks
    await db.execute(
        delete(KBChunk).where(KBChunk.document_id == doc_id)
    )

    doc.deleted_at = datetime.now(timezone.utc)
    doc.deleted_by = current_user.id
    await db.flush()
