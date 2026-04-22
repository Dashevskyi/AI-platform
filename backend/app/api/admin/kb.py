"""
Admin CRUD for tenant knowledge base documents.
Supports text, URL, and file upload sources with vector embeddings.
"""
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File, Form, status
from sqlalchemy import select, func, delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models.admin_user import AdminUser
from app.models.tenant import Tenant
from app.models.kb_document import KnowledgeBaseDocument
from app.models.kb_chunk import KBChunk
from app.schemas.kb import KBDocumentCreate, KBDocumentUpdate, KBDocumentResponse
from app.schemas.common import PaginatedResponse
from app.api.deps import require_role
from app.services.kb.embedder import (
    fetch_url_content,
    extract_file_content,
    process_document,
)

router = APIRouter(
    prefix="/api/admin/tenants/{tenant_id}/kb",
    tags=["admin-kb"],
    dependencies=[Depends(require_role("superadmin", "tenant_admin"))],
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
