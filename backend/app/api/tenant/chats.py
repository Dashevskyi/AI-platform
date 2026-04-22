"""
Tenant-facing chat and messaging endpoints.
Authenticated by tenant API key.
"""
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Form, File, UploadFile, status
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models.tenant import Tenant
from app.models.chat import Chat
from app.models.message import Message
from app.models.message_attachment import MessageAttachment
from app.schemas.chat import ChatCreate, ChatResponse, MessageSend, MessageResponse
from app.schemas.attachment import AttachmentBrief
from app.schemas.common import PaginatedResponse
from app.api.deps import get_current_tenant_from_key

router = APIRouter(
    prefix="/api/tenants/{tenant_id}/chats",
    tags=["tenant-chats"],
)


def _chat_to_response(c: Chat) -> ChatResponse:
    return ChatResponse(
        id=str(c.id),
        tenant_id=str(c.tenant_id),
        title=c.title,
        description=c.description,
        status=c.status,
        created_by=c.created_by,
        created_at=c.created_at,
        updated_at=c.updated_at,
    )


def _msg_to_response(m: Message) -> MessageResponse:
    return MessageResponse(
        id=str(m.id),
        tenant_id=str(m.tenant_id),
        chat_id=str(m.chat_id),
        role=m.role,
        content=m.content,
        prompt_tokens=m.prompt_tokens,
        completion_tokens=m.completion_tokens,
        total_tokens=m.total_tokens,
        latency_ms=m.latency_ms,
        status=m.status,
        created_at=m.created_at,
    )


def _verify_tenant_access(tenant_id: uuid.UUID, tenant: Tenant):
    """Verify the authenticated tenant matches the path tenant_id."""
    if tenant.id != tenant_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="API key does not belong to this tenant.",
        )


@router.get("/", response_model=PaginatedResponse[ChatResponse])
async def list_chats(
    tenant_id: uuid.UUID,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    tenant: Tenant = Depends(get_current_tenant_from_key),
):
    _verify_tenant_access(tenant_id, tenant)

    query = (
        select(Chat)
        .where(Chat.tenant_id == tenant_id, Chat.deleted_at.is_(None))
        .order_by(Chat.created_at.desc())
    )

    count_q = select(func.count()).select_from(query.subquery())
    total = (await db.execute(count_q)).scalar()

    items = (
        await db.execute(query.offset((page - 1) * page_size).limit(page_size))
    ).scalars().all()

    return PaginatedResponse[ChatResponse](
        items=[_chat_to_response(c) for c in items],
        total_count=total,
        page=page,
        page_size=page_size,
    )


@router.post("/", response_model=ChatResponse, status_code=status.HTTP_201_CREATED)
async def create_chat(
    tenant_id: uuid.UUID,
    body: ChatCreate,
    db: AsyncSession = Depends(get_db),
    tenant: Tenant = Depends(get_current_tenant_from_key),
):
    _verify_tenant_access(tenant_id, tenant)

    chat = Chat(
        tenant_id=tenant_id,
        title=body.title,
        description=body.description,
    )
    db.add(chat)
    await db.flush()
    await db.refresh(chat)
    return _chat_to_response(chat)


@router.get("/{chat_id}", response_model=ChatResponse)
async def get_chat(
    tenant_id: uuid.UUID,
    chat_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    tenant: Tenant = Depends(get_current_tenant_from_key),
):
    _verify_tenant_access(tenant_id, tenant)

    result = await db.execute(
        select(Chat).where(
            Chat.id == chat_id,
            Chat.tenant_id == tenant_id,
            Chat.deleted_at.is_(None),
        )
    )
    chat = result.scalars().first()
    if not chat:
        raise HTTPException(status_code=404, detail="Chat not found.")
    return _chat_to_response(chat)


@router.get("/{chat_id}/messages", response_model=PaginatedResponse[MessageResponse])
async def list_messages(
    tenant_id: uuid.UUID,
    chat_id: uuid.UUID,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    tenant: Tenant = Depends(get_current_tenant_from_key),
):
    _verify_tenant_access(tenant_id, tenant)

    # Verify chat exists and belongs to tenant
    chat_result = await db.execute(
        select(Chat).where(
            Chat.id == chat_id,
            Chat.tenant_id == tenant_id,
            Chat.deleted_at.is_(None),
        )
    )
    if not chat_result.scalars().first():
        raise HTTPException(status_code=404, detail="Chat not found.")

    query = (
        select(Message)
        .where(Message.chat_id == chat_id, Message.tenant_id == tenant_id)
        .order_by(Message.created_at.asc())
    )

    count_q = select(func.count()).select_from(query.subquery())
    total = (await db.execute(count_q)).scalar()

    items = (
        await db.execute(query.offset((page - 1) * page_size).limit(page_size))
    ).scalars().all()

    return PaginatedResponse[MessageResponse](
        items=[_msg_to_response(m) for m in items],
        total_count=total,
        page=page,
        page_size=page_size,
    )


@router.post("/{chat_id}/messages", response_model=MessageResponse, status_code=status.HTTP_201_CREATED)
async def send_message(
    tenant_id: uuid.UUID,
    chat_id: uuid.UUID,
    body: MessageSend,
    db: AsyncSession = Depends(get_db),
    tenant: Tenant = Depends(get_current_tenant_from_key),
):
    _verify_tenant_access(tenant_id, tenant)

    # Verify chat exists and belongs to tenant
    chat_result = await db.execute(
        select(Chat).where(
            Chat.id == chat_id,
            Chat.tenant_id == tenant_id,
            Chat.deleted_at.is_(None),
        )
    )
    if not chat_result.scalars().first():
        raise HTTPException(status_code=404, detail="Chat not found.")

    # 1. Check idempotency_key -- if a message with same key exists, return it
    if body.idempotency_key:
        existing_result = await db.execute(
            select(Message).where(Message.idempotency_key == body.idempotency_key)
        )
        existing = existing_result.scalars().first()
        if existing:
            return _msg_to_response(existing)

    # 2. Save user message
    user_message = Message(
        tenant_id=tenant_id,
        chat_id=chat_id,
        role="user",
        content=body.content,
        idempotency_key=body.idempotency_key,
        status="sent",
    )
    db.add(user_message)
    await db.flush()
    await db.refresh(user_message)

    # 3. Call LLM pipeline service
    try:
        from app.services.llm.pipeline import chat_completion

        llm_result = await chat_completion(
            tenant_id=str(tenant_id),
            chat_id=str(chat_id),
            user_content=body.content,
            db=db,
            user_message_id=str(user_message.id),
        )

        assistant_content = llm_result.get("content", "")
        prompt_tokens = llm_result.get("prompt_tokens")
        completion_tokens = llm_result.get("completion_tokens")
        total_tokens = llm_result.get("total_tokens")
        latency_ms = llm_result.get("latency_ms")
    except ImportError:
        # LLM service not yet implemented -- return placeholder
        assistant_content = "[LLM service not available]"
        prompt_tokens = None
        completion_tokens = None
        total_tokens = None
        latency_ms = None
    except Exception as exc:
        # Save error message
        error_message = Message(
            tenant_id=tenant_id,
            chat_id=chat_id,
            role="assistant",
            content=f"Error: {str(exc)[:500]}",
            status="error",
        )
        db.add(error_message)
        await db.flush()
        await db.refresh(error_message)
        return _msg_to_response(error_message)

    # 4. Save assistant message with token stats
    assistant_message = Message(
        tenant_id=tenant_id,
        chat_id=chat_id,
        role="assistant",
        content=assistant_content,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
        latency_ms=latency_ms,
        status="sent",
    )
    db.add(assistant_message)
    await db.flush()
    await db.refresh(assistant_message)

    # 5. Return assistant MessageResponse
    return _msg_to_response(assistant_message)


@router.post("/{chat_id}/messages/upload", response_model=MessageResponse, status_code=status.HTTP_201_CREATED)
async def send_message_with_files(
    tenant_id: uuid.UUID,
    chat_id: uuid.UUID,
    content: str = Form(...),
    idempotency_key: Optional[str] = Form(None),
    files: list[UploadFile] = File(default=[]),
    db: AsyncSession = Depends(get_db),
    tenant: Tenant = Depends(get_current_tenant_from_key),
):
    """Send a message with file attachments via tenant API."""
    _verify_tenant_access(tenant_id, tenant)

    chat_result = await db.execute(
        select(Chat).where(Chat.id == chat_id, Chat.tenant_id == tenant_id, Chat.deleted_at.is_(None))
    )
    if not chat_result.scalars().first():
        raise HTTPException(status_code=404, detail="Chat not found.")

    if idempotency_key:
        existing = (await db.execute(
            select(Message).where(Message.idempotency_key == idempotency_key)
        )).scalars().first()
        if existing:
            return _msg_to_response(existing)

    user_message = Message(
        tenant_id=tenant_id, chat_id=chat_id, role="user",
        content=content, idempotency_key=idempotency_key, status="sent",
    )
    db.add(user_message)
    await db.flush()
    await db.refresh(user_message)

    # Process file attachments
    if files:
        from app.services.storage import save_file, get_file_type
        from app.services.attachments.processor import process_attachment

        for upload_file in files:
            if not upload_file.filename:
                continue
            file_bytes = await upload_file.read()
            if not file_bytes:
                continue

            file_type = get_file_type(upload_file.filename)
            storage_path = await save_file(
                str(tenant_id), str(chat_id), upload_file.filename, file_bytes
            )

            att = MessageAttachment(
                message_id=user_message.id,
                tenant_id=tenant_id,
                chat_id=chat_id,
                filename=upload_file.filename,
                file_type=file_type,
                file_size_bytes=len(file_bytes),
                storage_path=storage_path,
                processing_status="pending",
            )
            db.add(att)
            await db.flush()
            await db.refresh(att)

            await process_attachment(att.id, tenant_id, db)

    # Call LLM pipeline
    try:
        from app.services.llm.pipeline import chat_completion

        llm_result = await chat_completion(
            tenant_id=str(tenant_id), chat_id=str(chat_id),
            user_content=content, db=db,
            user_message_id=str(user_message.id),
        )

        assistant_content = llm_result.get("content", "")
        prompt_tokens = llm_result.get("prompt_tokens")
        completion_tokens = llm_result.get("completion_tokens")
        total_tokens = llm_result.get("total_tokens")
        latency_ms = llm_result.get("latency_ms")
    except Exception as exc:
        error_message = Message(
            tenant_id=tenant_id, chat_id=chat_id, role="assistant",
            content=f"Error: {str(exc)[:500]}", status="error",
        )
        db.add(error_message)
        await db.flush()
        await db.refresh(error_message)
        return _msg_to_response(error_message)

    assistant_message = Message(
        tenant_id=tenant_id, chat_id=chat_id, role="assistant",
        content=assistant_content, prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens, total_tokens=total_tokens,
        latency_ms=latency_ms, status="sent",
    )
    db.add(assistant_message)
    await db.flush()
    await db.refresh(assistant_message)
    return _msg_to_response(assistant_message)


@router.get("/{chat_id}/attachments", response_model=list[AttachmentBrief])
async def list_attachments(
    tenant_id: uuid.UUID,
    chat_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    tenant: Tenant = Depends(get_current_tenant_from_key),
):
    """List all attachments in a chat."""
    _verify_tenant_access(tenant_id, tenant)

    result = await db.execute(
        select(MessageAttachment)
        .where(
            MessageAttachment.chat_id == chat_id,
            MessageAttachment.tenant_id == tenant_id,
        )
        .order_by(MessageAttachment.created_at.desc())
    )
    attachments = result.scalars().all()

    return [
        AttachmentBrief(
            id=str(a.id),
            filename=a.filename,
            file_type=a.file_type,
            file_size_bytes=a.file_size_bytes,
            processing_status=a.processing_status,
            summary=a.summary,
        )
        for a in attachments
    ]
