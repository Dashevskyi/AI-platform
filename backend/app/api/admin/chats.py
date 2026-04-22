"""
Admin endpoints for tenant chats.
"""
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Form, File, UploadFile, status
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models.admin_user import AdminUser
from app.models.tenant import Tenant
from app.models.chat import Chat
from app.models.message import Message
from app.models.message_attachment import MessageAttachment
from app.schemas.chat import ChatCreate, ChatUpdate, ChatResponse, MessageSend, MessageResponse
from app.schemas.attachment import AttachmentResponse, AttachmentBrief
from app.schemas.common import PaginatedResponse
from app.api.deps import require_role

router = APIRouter(
    prefix="/api/admin/tenants/{tenant_id}/chats",
    tags=["admin-chats"],
    dependencies=[Depends(require_role("superadmin", "tenant_admin"))],
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


async def _verify_tenant(tenant_id: uuid.UUID, db: AsyncSession) -> Tenant:
    result = await db.execute(
        select(Tenant).where(Tenant.id == tenant_id, Tenant.deleted_at.is_(None))
    )
    tenant = result.scalars().first()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found.")
    return tenant


@router.get("/", response_model=PaginatedResponse[ChatResponse])
async def list_chats(
    tenant_id: uuid.UUID,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status_filter: str | None = Query(None, alias="status"),
    search: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
):
    await _verify_tenant(tenant_id, db)

    query = select(Chat).where(
        Chat.tenant_id == tenant_id,
        Chat.deleted_at.is_(None),
    )

    if status_filter:
        query = query.where(Chat.status == status_filter)
    if search:
        pattern = f"%{search}%"
        query = query.where(
            (Chat.title.ilike(pattern)) | (Chat.description.ilike(pattern))
        )

    query = query.order_by(Chat.created_at.desc())

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


@router.get("/{chat_id}", response_model=ChatResponse)
async def get_chat(
    tenant_id: uuid.UUID,
    chat_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    await _verify_tenant(tenant_id, db)

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


@router.patch("/{chat_id}", response_model=ChatResponse)
async def update_chat(
    tenant_id: uuid.UUID,
    chat_id: uuid.UUID,
    body: ChatUpdate,
    db: AsyncSession = Depends(get_db),
):
    await _verify_tenant(tenant_id, db)

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

    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(chat, field, value)

    await db.flush()
    await db.refresh(chat)
    return _chat_to_response(chat)


@router.delete("/{chat_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_chat(
    tenant_id: uuid.UUID,
    chat_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: AdminUser = Depends(require_role("superadmin", "tenant_admin")),
):
    await _verify_tenant(tenant_id, db)

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

    chat.deleted_at = datetime.now(timezone.utc)
    chat.deleted_by = current_user.id
    await db.flush()


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


@router.post("/", response_model=ChatResponse, status_code=status.HTTP_201_CREATED)
async def create_chat(
    tenant_id: uuid.UUID,
    body: ChatCreate,
    db: AsyncSession = Depends(get_db),
    current_user: AdminUser = Depends(require_role("superadmin", "tenant_admin")),
):
    await _verify_tenant(tenant_id, db)
    chat = Chat(
        tenant_id=tenant_id,
        title=body.title,
        description=body.description,
        created_by=current_user.login,
    )
    db.add(chat)
    await db.flush()
    await db.refresh(chat)
    return _chat_to_response(chat)


@router.get("/{chat_id}/messages", response_model=PaginatedResponse[MessageResponse])
async def list_messages(
    tenant_id: uuid.UUID,
    chat_id: uuid.UUID,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
):
    await _verify_tenant(tenant_id, db)

    chat_result = await db.execute(
        select(Chat).where(Chat.id == chat_id, Chat.tenant_id == tenant_id, Chat.deleted_at.is_(None))
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

    items = (await db.execute(query.offset((page - 1) * page_size).limit(page_size))).scalars().all()

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
    current_user: AdminUser = Depends(require_role("superadmin", "tenant_admin")),
):
    await _verify_tenant(tenant_id, db)

    chat_result = await db.execute(
        select(Chat).where(Chat.id == chat_id, Chat.tenant_id == tenant_id, Chat.deleted_at.is_(None))
    )
    if not chat_result.scalars().first():
        raise HTTPException(status_code=404, detail="Chat not found.")

    # Idempotency check
    if body.idempotency_key:
        existing = (await db.execute(
            select(Message).where(Message.idempotency_key == body.idempotency_key)
        )).scalars().first()
        if existing:
            return _msg_to_response(existing)

    # Save user message
    user_message = Message(
        tenant_id=tenant_id, chat_id=chat_id, role="user",
        content=body.content, idempotency_key=body.idempotency_key, status="sent",
    )
    db.add(user_message)
    await db.flush()

    # Call LLM pipeline
    try:
        from app.services.llm.pipeline import chat_completion
        llm_result = await chat_completion(
            tenant_id=str(tenant_id), chat_id=str(chat_id),
            user_content=body.content, db=db,
            user_message_id=str(user_message.id),
        )
        assistant_content = llm_result.get("content", "")
        prompt_tokens = llm_result.get("prompt_tokens")
        completion_tokens = llm_result.get("completion_tokens")
        total_tokens = llm_result.get("total_tokens")
        latency_ms = llm_result.get("latency_ms")
        msg_status = "sent"
    except Exception as exc:
        assistant_content = f"Ошибка: {str(exc)[:500]}"
        prompt_tokens = completion_tokens = total_tokens = None
        latency_ms = None
        msg_status = "error"

    assistant_message = Message(
        tenant_id=tenant_id, chat_id=chat_id, role="assistant",
        content=assistant_content, prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens, total_tokens=total_tokens,
        latency_ms=latency_ms, status=msg_status,
    )
    db.add(assistant_message)
    await db.flush()
    await db.refresh(assistant_message)
    return _msg_to_response(assistant_message)


@router.post("/{chat_id}/messages/upload", response_model=MessageResponse, status_code=status.HTTP_201_CREATED)
async def send_message_with_files(
    tenant_id: uuid.UUID,
    chat_id: uuid.UUID,
    content: str = Form(...),
    idempotency_key: Optional[str] = Form(None),
    files: list[UploadFile] = File(default=[]),
    db: AsyncSession = Depends(get_db),
    current_user: AdminUser = Depends(require_role("superadmin", "tenant_admin")),
):
    """Send a message with file attachments. Files are processed and available via tool calling."""
    await _verify_tenant(tenant_id, db)

    chat_result = await db.execute(
        select(Chat).where(Chat.id == chat_id, Chat.tenant_id == tenant_id, Chat.deleted_at.is_(None))
    )
    if not chat_result.scalars().first():
        raise HTTPException(status_code=404, detail="Chat not found.")

    # Idempotency check
    if idempotency_key:
        existing = (await db.execute(
            select(Message).where(Message.idempotency_key == idempotency_key)
        )).scalars().first()
        if existing:
            return _msg_to_response(existing)

    # Save user message
    user_message = Message(
        tenant_id=tenant_id, chat_id=chat_id, role="user",
        content=content, idempotency_key=idempotency_key, status="sent",
    )
    db.add(user_message)
    await db.flush()
    await db.refresh(user_message)

    # Process file attachments
    attachment_ids: list[str] = []
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
            attachment_ids.append(str(att.id))

            # Process attachment synchronously (extract, chunk, embed)
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
        msg_status = "sent"
    except Exception as exc:
        assistant_content = f"Ошибка: {str(exc)[:500]}"
        prompt_tokens = completion_tokens = total_tokens = None
        latency_ms = None
        msg_status = "error"

    assistant_message = Message(
        tenant_id=tenant_id, chat_id=chat_id, role="assistant",
        content=assistant_content, prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens, total_tokens=total_tokens,
        latency_ms=latency_ms, status=msg_status,
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
):
    """List all attachments in a chat."""
    await _verify_tenant(tenant_id, db)

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
