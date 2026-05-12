"""
Tenant-facing chat and messaging endpoints.
Authenticated by tenant API key.
"""
import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Form, File, UploadFile, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select, func, or_
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

from app.core.database import get_db
from app.models.tenant import Tenant
from app.models.chat import Chat
from app.models.message import Message
from app.models.message_attachment import MessageAttachment
from app.schemas.chat import ChatCreate, ChatResponse, MessageSend, MessageResponse, PublicMessageResponse
from app.schemas.attachment import AttachmentBrief
from app.schemas.common import PaginatedResponse
from app.api.deps import TenantAuthContext, get_current_tenant_auth_context

router = APIRouter(
    prefix="/api/tenants/{tenant_id}/chats",
    tags=["tenant-chats"],
)


def _chat_to_response(c: Chat) -> ChatResponse:
    return ChatResponse(
        id=str(c.id),
        tenant_id=str(c.tenant_id),
        api_key_id=str(c.api_key_id) if c.api_key_id else None,
        title=c.title,
        description=c.description,
        status=c.status,
        created_by=c.created_by,
        created_at=c.created_at,
        updated_at=c.updated_at,
    )


def _msg_to_response(m: Message) -> PublicMessageResponse:
    """End-user-facing response — drops all internal metadata."""
    return PublicMessageResponse(
        id=str(m.id),
        chat_id=str(m.chat_id),
        role=m.role,
        content=m.content,
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


def _build_scoped_idempotency_key(tenant_id: uuid.UUID, chat_id: uuid.UUID, raw_key: str | None) -> str | None:
    if not raw_key:
        return None
    return f"{tenant_id}:{chat_id}:{raw_key}"


def _chat_scope_query(tenant_id: uuid.UUID, api_key_id: uuid.UUID):
    return select(Chat).where(
        Chat.tenant_id == tenant_id,
        or_(
            Chat.api_key_id == api_key_id,
            Chat.api_key_id.is_(None),
        ),
        Chat.deleted_at.is_(None),
    )


@router.get("/", response_model=PaginatedResponse[ChatResponse])
async def list_chats(
    tenant_id: uuid.UUID,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    auth: TenantAuthContext = Depends(get_current_tenant_auth_context),
):
    _verify_tenant_access(tenant_id, auth.tenant)

    query = (
        _chat_scope_query(tenant_id, auth.api_key.id)
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
    auth: TenantAuthContext = Depends(get_current_tenant_auth_context),
):
    _verify_tenant_access(tenant_id, auth.tenant)

    chat = Chat(
        tenant_id=tenant_id,
        api_key_id=auth.api_key.id,
        title=body.title,
        description=body.description,
        created_by=auth.api_key.name,
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
    auth: TenantAuthContext = Depends(get_current_tenant_auth_context),
):
    _verify_tenant_access(tenant_id, auth.tenant)

    result = await db.execute(
        _chat_scope_query(tenant_id, auth.api_key.id).where(Chat.id == chat_id)
    )
    chat = result.scalars().first()
    if not chat:
        raise HTTPException(status_code=404, detail="Chat not found.")
    return _chat_to_response(chat)


@router.get("/{chat_id}/messages", response_model=PaginatedResponse[PublicMessageResponse])
async def list_messages(
    tenant_id: uuid.UUID,
    chat_id: uuid.UUID,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    auth: TenantAuthContext = Depends(get_current_tenant_auth_context),
):
    _verify_tenant_access(tenant_id, auth.tenant)

    # Verify chat exists and belongs to tenant
    chat_result = await db.execute(
        _chat_scope_query(tenant_id, auth.api_key.id).where(Chat.id == chat_id)
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

    return PaginatedResponse[PublicMessageResponse](
        items=[_msg_to_response(m) for m in items],
        total_count=total,
        page=page,
        page_size=page_size,
    )


@router.post("/{chat_id}/messages", response_model=PublicMessageResponse, status_code=status.HTTP_201_CREATED)
async def send_message(
    tenant_id: uuid.UUID,
    chat_id: uuid.UUID,
    body: MessageSend,
    db: AsyncSession = Depends(get_db),
    auth: TenantAuthContext = Depends(get_current_tenant_auth_context),
):
    _verify_tenant_access(tenant_id, auth.tenant)

    # Verify chat exists and belongs to tenant
    chat_result = await db.execute(
        _chat_scope_query(tenant_id, auth.api_key.id).where(Chat.id == chat_id)
    )
    if not chat_result.scalars().first():
        raise HTTPException(status_code=404, detail="Chat not found.")

    scoped_idempotency_key = _build_scoped_idempotency_key(tenant_id, chat_id, body.idempotency_key)

    # 1. Check idempotency_key inside tenant/chat scope
    if scoped_idempotency_key:
        existing_result = await db.execute(
            select(Message).where(
                Message.tenant_id == tenant_id,
                Message.chat_id == chat_id,
                Message.idempotency_key == scoped_idempotency_key,
            )
        )
        existing = existing_result.scalars().first()
        if existing:
            return _msg_to_response(existing)

    # 2. Save user message and commit before long-running LLM work
    user_message = Message(
        tenant_id=tenant_id,
        chat_id=chat_id,
        role="user",
        content=body.content,
        idempotency_key=scoped_idempotency_key,
        status="sent",
    )
    db.add(user_message)
    await db.flush()
    await db.refresh(user_message)
    await db.commit()

    # 3. Call LLM pipeline service
    try:
        from app.services.llm.pipeline import chat_completion
        from app.services.throttle import ThrottleRejected

        try:
            llm_result = await chat_completion(
                tenant_id=str(tenant_id),
                chat_id=str(chat_id),
                user_content=body.content,
                db=db,
                user_message_id=str(user_message.id),
                api_key_id=str(auth.api_key.id),
            )
        except ThrottleRejected as exc:
            raise HTTPException(
                status_code=429,
                detail=str(exc),
                headers={"Retry-After": str(exc.retry_after)},
            )

        assistant_content = llm_result.get("content", "")
        prompt_tokens = llm_result.get("prompt_tokens")
        completion_tokens = llm_result.get("completion_tokens")
        total_tokens = llm_result.get("total_tokens")
        latency_ms = llm_result.get("latency_ms")
        assistant_metadata = {
            "time_to_first_token_ms": llm_result.get("time_to_first_token_ms"),
            "provider_type": llm_result.get("provider_type"),
            "model_name": llm_result.get("model_name"),
            "correlation_id": llm_result.get("correlation_id"),
            "reasoning": llm_result.get("reasoning"),
            "tool_calls_count": llm_result.get("tool_calls_count"),
            "finish_reason": llm_result.get("finish_reason"),
            "response_summary": llm_result.get("response_summary"),
            "tool_result_summary": llm_result.get("tool_result_summary"),
            "attachment_summary": llm_result.get("attachment_summary"),
            "context_card": llm_result.get("context_card"),
            "history_exclude": llm_result.get("history_exclude"),
        }
        assistant_status = "sent"
    except ImportError:
        assistant_content = "[LLM service not available]"
        prompt_tokens = None
        completion_tokens = None
        total_tokens = None
        latency_ms = None
        assistant_metadata = None
        assistant_status = "error"
    except Exception as exc:
        assistant_content = f"Error: {str(exc)[:500]}"
        prompt_tokens = None
        completion_tokens = None
        total_tokens = None
        latency_ms = None
        assistant_metadata = None
        assistant_status = "error"

    # 4. Save assistant message with token stats
    assistant_message = Message(
        tenant_id=tenant_id,
        chat_id=chat_id,
        role="assistant",
        content=assistant_content,
        metadata_json=assistant_metadata,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
        latency_ms=latency_ms,
        status=assistant_status,
    )
    db.add(assistant_message)
    await db.flush()
    await db.commit()
    await db.refresh(assistant_message)

    # 5. Return assistant MessageResponse
    return _msg_to_response(assistant_message)


def _sse_format(event_type: str, payload: dict) -> str:
    return f"event: {event_type}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


# ----- Public SSE event whitelist -----
# End-user clients should NOT see internal pipeline details
# (kb_search_*, tool_call_*, provider_call_*, reasoning, reasoning_chunk).
# They get just enough to render: stream open, content chunks, final result.
PUBLIC_SSE_EVENTS = {
    "stream_open",
    "content_chunk",
    "done",
    "final",
    "error",
    "throttle_rejected",
    "merge_pending",
    "merge_start",
    "tool_call_start",
    "tool_call_done",
}


@router.post("/{chat_id}/messages/stream")
async def send_message_stream(
    tenant_id: uuid.UUID,
    chat_id: uuid.UUID,
    body: MessageSend,
    db: AsyncSession = Depends(get_db),
    auth: TenantAuthContext = Depends(get_current_tenant_auth_context),
):
    """SSE streaming variant — sanitized event set, no internal trail leaks."""
    _verify_tenant_access(tenant_id, auth.tenant)

    chat_result = await db.execute(
        _chat_scope_query(tenant_id, auth.api_key.id).where(Chat.id == chat_id)
    )
    if not chat_result.scalars().first():
        raise HTTPException(status_code=404, detail="Chat not found.")

    scoped_idempotency_key = _build_scoped_idempotency_key(tenant_id, chat_id, body.idempotency_key)

    if scoped_idempotency_key:
        existing = (await db.execute(
            select(Message).where(
                Message.tenant_id == tenant_id,
                Message.chat_id == chat_id,
                Message.idempotency_key == scoped_idempotency_key,
            )
        )).scalars().first()
        if existing:
            async def _idem_gen():
                yield _sse_format("done", {
                    "assistant_message_id": str(existing.id),
                    "content": existing.content,
                    "idempotent_replay": True,
                })
            return StreamingResponse(_idem_gen(), media_type="text/event-stream")

    user_message = Message(
        tenant_id=tenant_id, chat_id=chat_id, role="user",
        content=body.content, idempotency_key=scoped_idempotency_key, status="sent",
    )
    db.add(user_message)
    await db.flush()
    await db.refresh(user_message)
    await db.commit()
    user_message_id = str(user_message.id)
    api_key_id_str = str(auth.api_key.id)

    queue: asyncio.Queue[tuple[str, dict] | None] = asyncio.Queue()

    async def emitter(event_type: str, payload: dict) -> None:
        # Only public events are forwarded to the client. Internal trail
        # is dropped at the source — no chance of leak.
        if event_type not in PUBLIC_SSE_EVENTS:
            return
        await queue.put((event_type, payload))

    # Determine merge mode
    tenant_row = (await db.execute(
        select(Tenant).where(Tenant.id == tenant_id, Tenant.deleted_at.is_(None))
    )).scalar_one_or_none()
    merge_enabled = bool(tenant_row and tenant_row.merge_messages_enabled and tenant_row.merge_window_ms > 0)
    merge_window_ms = int(tenant_row.merge_window_ms) if tenant_row else 1500

    async def runner() -> dict | None:
        from app.services.llm.pipeline import chat_completion
        from app.services.throttle import ThrottleRejected
        from app.services.message_merger import submit_or_merge
        from app.core.database import async_session
        try:
            if merge_enabled:
                merged_result = await submit_or_merge(
                    tenant_id=str(tenant_id),
                    chat_id=str(chat_id),
                    api_key_id=api_key_id_str,
                    user_message_id=user_message_id,
                    content=body.content,
                    on_event=emitter,
                    merge_window_ms=merge_window_ms,
                )
                await queue.put(None)
                return {"_merged": True, "data": merged_result}
            async with async_session() as fresh_db:
                result = await chat_completion(
                    tenant_id=str(tenant_id), chat_id=str(chat_id),
                    user_content=body.content, db=fresh_db,
                    user_message_id=user_message_id,
                    api_key_id=api_key_id_str,
                    on_event=emitter,
                )
                await fresh_db.commit()
            await queue.put(None)
            return {"_merged": False, "data": result}
        except ThrottleRejected as exc:
            await queue.put(("throttle_rejected", {"message": str(exc), "retry_after": exc.retry_after}))
            await queue.put(None)
            return None
        except Exception as exc:
            logger.exception("[tenant-stream] pipeline runner failed for tenant=%s chat=%s", tenant_id, chat_id)
            await queue.put(("error", {"message": str(exc)[:500]}))
            await queue.put(None)
            return None

    pipeline_task = asyncio.create_task(runner())

    async def event_gen():
        try:
            yield _sse_format("stream_open", {"chat_id": str(chat_id)})
            while True:
                item = await queue.get()
                if item is None:
                    break
                event_type, payload = item
                yield _sse_format(event_type, payload)
            wrapped = await pipeline_task
            from app.core.database import async_session as _save_session
            if wrapped is None:
                async with _save_session() as save_db:
                    msg = Message(
                        tenant_id=tenant_id, chat_id=chat_id, role="assistant",
                        content="Ошибка обработки запроса.", status="error",
                    )
                    save_db.add(msg)
                    await save_db.flush()
                    await save_db.commit()
                    await save_db.refresh(msg)
                    msg_id = str(msg.id)
                yield _sse_format("final", {"assistant_message_id": msg_id})
                return

            if wrapped["_merged"]:
                merged = wrapped["data"]
                # Sanitized: don't echo internal metadata to public clients
                yield _sse_format("final", {
                    "assistant_message_id": merged["assistant_message_id"],
                })
                return

            llm_result = wrapped["data"]
            # Sanitized assistant_metadata for tenant — no events trail, no reasoning
            assistant_metadata = {
                # Keep these because they're useful for client UX without leaking internals:
                "finish_reason": llm_result.get("finish_reason"),
                "history_exclude": llm_result.get("history_exclude"),
            }
            async with _save_session() as save_db:
                msg = Message(
                    tenant_id=tenant_id, chat_id=chat_id, role="assistant",
                    content=llm_result.get("content", ""),
                    metadata_json=assistant_metadata,
                    prompt_tokens=llm_result.get("prompt_tokens"),
                    completion_tokens=llm_result.get("completion_tokens"),
                    total_tokens=llm_result.get("total_tokens"),
                    latency_ms=llm_result.get("latency_ms"),
                    status="sent",
                )
                save_db.add(msg)
                await save_db.flush()
                await save_db.commit()
                await save_db.refresh(msg)
                final_id = str(msg.id)
            yield _sse_format("final", {"assistant_message_id": final_id})
        except asyncio.CancelledError:
            pipeline_task.cancel()
            raise
        except Exception:
            logger.exception("[tenant-stream] event_gen failed for tenant=%s chat=%s", tenant_id, chat_id)
            raise

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"},
    )


@router.post("/{chat_id}/messages/upload", response_model=PublicMessageResponse, status_code=status.HTTP_201_CREATED)
async def send_message_with_files(
    tenant_id: uuid.UUID,
    chat_id: uuid.UUID,
    background_tasks: BackgroundTasks,
    content: str = Form(...),
    idempotency_key: Optional[str] = Form(None),
    files: list[UploadFile] = File(default=[]),
    db: AsyncSession = Depends(get_db),
    auth: TenantAuthContext = Depends(get_current_tenant_auth_context),
):
    """Send a message with file attachments via tenant API."""
    _verify_tenant_access(tenant_id, auth.tenant)

    chat_result = await db.execute(
        _chat_scope_query(tenant_id, auth.api_key.id).where(Chat.id == chat_id)
    )
    if not chat_result.scalars().first():
        raise HTTPException(status_code=404, detail="Chat not found.")

    scoped_idempotency_key = _build_scoped_idempotency_key(tenant_id, chat_id, idempotency_key)
    if scoped_idempotency_key:
        existing = (await db.execute(
            select(Message).where(
                Message.tenant_id == tenant_id,
                Message.chat_id == chat_id,
                Message.idempotency_key == scoped_idempotency_key,
            )
        )).scalars().first()
        if existing:
            return _msg_to_response(existing)

    user_message = Message(
        tenant_id=tenant_id, chat_id=chat_id, role="user",
        content=content, idempotency_key=scoped_idempotency_key, status="sent",
    )
    db.add(user_message)
    await db.flush()
    await db.refresh(user_message)

    attachment_ids: list[str] = []
    if files:
        from app.services.storage import save_file, get_file_type

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

    await db.commit()

    if attachment_ids:
        # Process attachments INLINE so the user gets a real answer in one
        # round trip. Background scheduling is a fallback for timeouts.
        from app.services.attachments.processor import process_attachment, process_attachment_background

        timeout_per_file = 90.0
        timed_out: list[str] = []
        for attachment_id in attachment_ids:
            try:
                await asyncio.wait_for(
                    process_attachment(uuid.UUID(attachment_id), tenant_id, db),
                    timeout=timeout_per_file,
                )
            except asyncio.TimeoutError:
                logger.warning("Attachment %s processing timed out (%.0fs); falling back to background", attachment_id, timeout_per_file)
                timed_out.append(attachment_id)
            except Exception:
                logger.exception("Attachment %s processing failed inline", attachment_id)
                timed_out.append(attachment_id)
        await db.commit()

        for attachment_id in timed_out:
            background_tasks.add_task(process_attachment_background, attachment_id, str(tenant_id))

    # Call LLM pipeline
    try:
        from app.services.llm.pipeline import chat_completion

        llm_result = await chat_completion(
            tenant_id=str(tenant_id), chat_id=str(chat_id),
            user_content=content, db=db,
            user_message_id=str(user_message.id),
            api_key_id=str(auth.api_key.id),
        )

        assistant_content = llm_result.get("content", "")
        prompt_tokens = llm_result.get("prompt_tokens")
        completion_tokens = llm_result.get("completion_tokens")
        total_tokens = llm_result.get("total_tokens")
        latency_ms = llm_result.get("latency_ms")
        assistant_metadata = {
            "time_to_first_token_ms": llm_result.get("time_to_first_token_ms"),
            "provider_type": llm_result.get("provider_type"),
            "model_name": llm_result.get("model_name"),
            "correlation_id": llm_result.get("correlation_id"),
            "reasoning": llm_result.get("reasoning"),
            "tool_calls_count": llm_result.get("tool_calls_count"),
            "finish_reason": llm_result.get("finish_reason"),
            "response_summary": llm_result.get("response_summary"),
            "tool_result_summary": llm_result.get("tool_result_summary"),
            "attachment_summary": llm_result.get("attachment_summary"),
            "context_card": llm_result.get("context_card"),
            "history_exclude": llm_result.get("history_exclude"),
        }
        assistant_status = "sent"
    except Exception as exc:
        assistant_content = f"Error: {str(exc)[:500]}"
        prompt_tokens = None
        completion_tokens = None
        total_tokens = None
        latency_ms = None
        assistant_metadata = None
        assistant_status = "error"

    assistant_message = Message(
        tenant_id=tenant_id, chat_id=chat_id, role="assistant",
        content=assistant_content, metadata_json=assistant_metadata, prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens, total_tokens=total_tokens,
        latency_ms=latency_ms, status=assistant_status,
    )
    db.add(assistant_message)
    await db.flush()
    await db.commit()
    await db.refresh(assistant_message)
    return _msg_to_response(assistant_message)


@router.get("/{chat_id}/attachments", response_model=list[AttachmentBrief])
async def list_attachments(
    tenant_id: uuid.UUID,
    chat_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    auth: TenantAuthContext = Depends(get_current_tenant_auth_context),
):
    """List all attachments in a chat."""
    _verify_tenant_access(tenant_id, auth.tenant)

    chat_result = await db.execute(
        _chat_scope_query(tenant_id, auth.api_key.id).where(Chat.id == chat_id)
    )
    if not chat_result.scalars().first():
        raise HTTPException(status_code=404, detail="Chat not found.")

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
