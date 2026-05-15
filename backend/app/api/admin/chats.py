"""
Admin endpoints for tenant chats.
"""
import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Form, File, UploadFile, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models.admin_user import AdminUser
from app.models.tenant import Tenant
from app.models.chat import Chat
from app.models.message import Message
from app.models.message_attachment import MessageAttachment
from app.models.artifact import Artifact
from app.schemas.chat import ChatCreate, ChatUpdate, ChatResponse, MessageSend, MessageResponse
from app.schemas.attachment import AttachmentResponse, AttachmentBrief
from app.schemas.artifact import ArtifactBrief, ArtifactDetail
from app.schemas.common import PaginatedResponse
from app.api.deps import require_role, require_tenant_access, require_permission

router = APIRouter(
    prefix="/api/admin/tenants/{tenant_id}/chats",
    tags=["admin-chats"],
    dependencies=[Depends(require_role("superadmin", "tenant_admin")), Depends(require_tenant_access), Depends(require_permission("chats"))],
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


async def _verify_tenant(tenant_id: uuid.UUID, db: AsyncSession) -> Tenant:
    result = await db.execute(
        select(Tenant).where(Tenant.id == tenant_id, Tenant.deleted_at.is_(None))
    )
    tenant = result.scalars().first()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found.")
    return tenant


def _build_scoped_idempotency_key(tenant_id: uuid.UUID, chat_id: uuid.UUID, raw_key: str | None) -> str | None:
    if not raw_key:
        return None
    return f"{tenant_id}:{chat_id}:{raw_key}"


async def _find_idempotent_response(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    chat_id: uuid.UUID,
    scoped_idempotency_key: str | None,
) -> Message | None:
    if not scoped_idempotency_key:
        return None
    existing = (
        await db.execute(
            select(Message).where(
                Message.tenant_id == tenant_id,
                Message.chat_id == chat_id,
                Message.idempotency_key == scoped_idempotency_key,
            )
        )
    ).scalars().first()
    if not existing:
        return None
    if existing.role == "assistant":
        return existing
    assistant = (
        await db.execute(
            select(Message)
            .where(
                Message.tenant_id == tenant_id,
                Message.chat_id == chat_id,
                Message.role == "assistant",
                Message.created_at >= existing.created_at,
            )
            .order_by(Message.created_at.asc())
            .limit(1)
        )
    ).scalars().first()
    return assistant or existing


@router.get("/", response_model=PaginatedResponse[ChatResponse])
async def list_chats(
    tenant_id: uuid.UUID,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status_filter: str | None = Query(None, alias="status"),
    search: str | None = Query(None),
    api_key_id: uuid.UUID | None = Query(None),
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
    if api_key_id:
        query = query.where(Chat.api_key_id == api_key_id)

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
    meta = m.metadata_json or {}
    return MessageResponse(
        id=str(m.id),
        tenant_id=str(m.tenant_id),
        chat_id=str(m.chat_id),
        role=m.role,
        content=m.content,
        metadata_json=m.metadata_json,
        prompt_tokens=m.prompt_tokens,
        completion_tokens=m.completion_tokens,
        total_tokens=m.total_tokens,
        latency_ms=m.latency_ms,
        time_to_first_token_ms=meta.get("time_to_first_token_ms"),
        provider_type=meta.get("provider_type"),
        model_name=meta.get("model_name"),
        correlation_id=meta.get("correlation_id"),
        tool_calls_count=meta.get("tool_calls_count"),
        finish_reason=meta.get("finish_reason"),
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
    chat_obj = chat_result.scalars().first()
    if not chat_obj:
        raise HTTPException(status_code=404, detail="Chat not found.")
    chat_api_key_id = str(chat_obj.api_key_id) if chat_obj.api_key_id else None

    scoped_idempotency_key = _build_scoped_idempotency_key(tenant_id, chat_id, body.idempotency_key)

    # Idempotency check
    if scoped_idempotency_key:
        existing = await _find_idempotent_response(db, tenant_id, chat_id, scoped_idempotency_key)
        if existing:
            return _msg_to_response(existing)

    # Save user message and commit before long-running LLM work
    user_message = Message(
        tenant_id=tenant_id, chat_id=chat_id, role="user",
        content=body.content, idempotency_key=scoped_idempotency_key, status="sent",
    )
    db.add(user_message)
    await db.flush()
    await db.refresh(user_message)
    await db.commit()

    # Call LLM pipeline
    try:
        from app.services.llm.pipeline import chat_completion
        from app.services.throttle import ThrottleRejected
        try:
            llm_result = await chat_completion(
                tenant_id=str(tenant_id), chat_id=str(chat_id),
                user_content=body.content, db=db,
                user_message_id=str(user_message.id),
                api_key_id=chat_api_key_id,
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
        msg_status = "sent"
    except Exception as exc:
        assistant_content = f"Ошибка: {str(exc)[:500]}"
        prompt_tokens = completion_tokens = total_tokens = None
        latency_ms = None
        assistant_metadata = None
        msg_status = "error"

    assistant_message = Message(
        tenant_id=tenant_id, chat_id=chat_id, role="assistant",
        content=assistant_content, metadata_json=assistant_metadata, prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens, total_tokens=total_tokens,
        latency_ms=latency_ms, status=msg_status,
    )
    db.add(assistant_message)
    await db.flush()
    await db.commit()
    await db.refresh(assistant_message)
    return _msg_to_response(assistant_message)


def _sse_format(event_type: str, payload: dict) -> str:
    return f"event: {event_type}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


@router.post("/{chat_id}/messages/stream")
async def send_message_stream(
    tenant_id: uuid.UUID,
    chat_id: uuid.UUID,
    body: MessageSend,
    db: AsyncSession = Depends(get_db),
):
    """
    SSE streaming variant of send_message.
    Emits events: pipeline_start, kb_search_start/done, provider_call_start/done,
    tool_call_start/done, done, error. Final 'done' event includes assistant_message_id.
    """
    await _verify_tenant(tenant_id, db)

    chat_result = await db.execute(
        select(Chat).where(Chat.id == chat_id, Chat.tenant_id == tenant_id, Chat.deleted_at.is_(None))
    )
    chat_obj = chat_result.scalars().first()
    if not chat_obj:
        raise HTTPException(status_code=404, detail="Chat not found.")
    chat_api_key_id = str(chat_obj.api_key_id) if chat_obj.api_key_id else None

    scoped_idempotency_key = _build_scoped_idempotency_key(tenant_id, chat_id, body.idempotency_key)

    if scoped_idempotency_key:
        existing = await _find_idempotent_response(db, tenant_id, chat_id, scoped_idempotency_key)
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

    queue: asyncio.Queue[tuple[str, dict] | None] = asyncio.Queue()
    trail: list[dict] = []
    TRAIL_KEEP_TYPES = {
        "kb_search_start", "kb_search_done",
        "provider_call_start", "provider_call_done",
        "tool_call_start", "tool_call_done",
        "reasoning", "error",
    }

    async def emitter(event_type: str, payload: dict) -> None:
        if event_type in TRAIL_KEEP_TYPES:
            trail.append({"type": event_type, "payload": payload})
        await queue.put((event_type, payload))

    # Determine merge mode for this tenant
    tenant_row = (await db.execute(
        select(Tenant).where(Tenant.id == tenant_id, Tenant.deleted_at.is_(None))
    )).scalar_one_or_none()
    merge_enabled = bool(tenant_row and tenant_row.merge_messages_enabled and tenant_row.merge_window_ms > 0)
    merge_window_ms = int(tenant_row.merge_window_ms) if tenant_row else 1500

    from app.core.database import async_session

    async def _save_assistant(
        content: str,
        status_: str,
        llm_result: dict | None = None,
    ) -> tuple[str, dict | None]:
        """Persist assistant message in a fresh session. Survives client disconnects."""
        if llm_result is not None:
            assistant_metadata = {
                "time_to_first_token_ms": llm_result.get("time_to_first_token_ms"),
                "provider_type": llm_result.get("provider_type"),
                "model_name": llm_result.get("model_name"),
                "correlation_id": llm_result.get("correlation_id"),
                "reasoning": llm_result.get("reasoning"),
                "events": trail,
                "tool_calls_count": llm_result.get("tool_calls_count"),
                "finish_reason": llm_result.get("finish_reason"),
                "response_summary": llm_result.get("response_summary"),
                "tool_result_summary": llm_result.get("tool_result_summary"),
                "attachment_summary": llm_result.get("attachment_summary"),
                "context_card": llm_result.get("context_card"),
                "history_exclude": llm_result.get("history_exclude"),
            }
            kwargs = {
                "prompt_tokens": llm_result.get("prompt_tokens"),
                "completion_tokens": llm_result.get("completion_tokens"),
                "total_tokens": llm_result.get("total_tokens"),
                "latency_ms": llm_result.get("latency_ms"),
            }
        else:
            assistant_metadata = None
            kwargs = {}
        async with async_session() as save_db:
            assistant_message = Message(
                tenant_id=tenant_id, chat_id=chat_id, role="assistant",
                content=content,
                metadata_json=assistant_metadata,
                status=status_,
                **kwargs,
            )
            save_db.add(assistant_message)
            await save_db.flush()
            await save_db.commit()
            await save_db.refresh(assistant_message)
            new_id = str(assistant_message.id)

        # Background resume generation for the (user, assistant) pair.
        if status_ == "sent" and user_message_id:
            try:
                import uuid as _uuid
                from app.services.resume_generator import generate_resume_for_pair
                asyncio.create_task(generate_resume_for_pair(
                    tenant_id=tenant_id,
                    chat_id=chat_id,
                    user_message_id=_uuid.UUID(str(user_message_id)),
                    assistant_message_id=_uuid.UUID(new_id),
                ))
            except Exception:
                logger.exception("[admin-stream] failed to schedule resume generation")
        return new_id, assistant_metadata

    async def runner() -> None:
        from app.services.llm.pipeline import chat_completion
        from app.services.throttle import ThrottleRejected
        from app.services.message_merger import submit_or_merge
        try:
            if merge_enabled:
                # Merger handles save + fan-out emit (uses its own session internally)
                merged_result = await submit_or_merge(
                    tenant_id=str(tenant_id),
                    chat_id=str(chat_id),
                    api_key_id=chat_api_key_id,
                    user_message_id=user_message_id,
                    content=body.content,
                    on_event=emitter,
                    merge_window_ms=merge_window_ms,
                )
                await queue.put(("final", {
                    "assistant_message_id": merged_result["assistant_message_id"],
                    "metadata": merged_result.get("metadata"),
                }))
                await queue.put(None)
                return
            # Direct path — fresh DB session, save survives client disconnect.
            async with async_session() as fresh_db:
                result = await chat_completion(
                    tenant_id=str(tenant_id), chat_id=str(chat_id),
                    user_content=body.content, db=fresh_db,
                    user_message_id=user_message_id,
                    api_key_id=chat_api_key_id,
                    on_event=emitter,
                )
                # Pipeline writes LLMRequestLog via db.add — commit before session closes
                await fresh_db.commit()
            final_id, assistant_metadata = await _save_assistant(
                result.get("content", "") or "",
                "sent",
                llm_result=result,
            )
            await queue.put(("final", {
                "assistant_message_id": final_id,
                "metadata": assistant_metadata,
            }))
            await queue.put(None)
        except ThrottleRejected as exc:
            await queue.put((
                "throttle_rejected",
                {"message": str(exc), "retry_after": exc.retry_after},
            ))
            try:
                final_id, _ = await _save_assistant(
                    f"Слишком много параллельных запросов. Попробуйте через {exc.retry_after} с.",
                    "error",
                )
                await queue.put(("final", {"assistant_message_id": final_id}))
            except Exception:
                logger.exception("[stream] failed to save throttle fallback message")
            await queue.put(None)
        except Exception as exc:
            logger.exception("[stream] pipeline runner failed for tenant=%s chat=%s", tenant_id, chat_id)
            await queue.put(("error", {"message": str(exc)[:500]}))
            try:
                final_id, _ = await _save_assistant("Ошибка обработки запроса.", "error")
                await queue.put(("final", {"assistant_message_id": final_id}))
            except Exception:
                logger.exception("[stream] failed to save error fallback message")
            await queue.put(None)

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
        except asyncio.CancelledError:
            # Client disconnected — DO NOT cancel pipeline_task; let it finish saving
            # the assistant message in the background. CRM PHP proxy 30s timeout was
            # losing replies because save lived here in event_gen.
            logger.info(
                "[admin-stream] client disconnected; pipeline continues in background "
                "(tenant=%s chat=%s)", tenant_id, chat_id,
            )
            raise
        except Exception:
            logger.exception("[admin-stream] event_gen failed for tenant=%s chat=%s", tenant_id, chat_id)
            raise

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",  # nginx: don't buffer
        },
    )


@router.post("/{chat_id}/messages/upload", response_model=MessageResponse, status_code=status.HTTP_201_CREATED)
async def send_message_with_files(
    tenant_id: uuid.UUID,
    chat_id: uuid.UUID,
    background_tasks: BackgroundTasks,
    content: str = Form(...),
    idempotency_key: Optional[str] = Form(None),
    files: list[UploadFile] = File(default=[]),
    # Comma-separated UUIDs of drafts uploaded via .../attachments/draft.
    attachment_ids: Optional[str] = Form(default=None),
    db: AsyncSession = Depends(get_db),
    current_user: AdminUser = Depends(require_role("superadmin", "tenant_admin")),
):
    """Send a message with file attachments. Files are processed and available via tool calling.
    Accepts either raw `files` (inline processing) or pre-uploaded draft `attachment_ids`."""
    await _verify_tenant(tenant_id, db)

    chat_result = await db.execute(
        select(Chat).where(Chat.id == chat_id, Chat.tenant_id == tenant_id, Chat.deleted_at.is_(None))
    )
    if not chat_result.scalars().first():
        raise HTTPException(status_code=404, detail="Chat not found.")

    scoped_idempotency_key = _build_scoped_idempotency_key(tenant_id, chat_id, idempotency_key)

    # Idempotency check
    if scoped_idempotency_key:
        existing = await _find_idempotent_response(db, tenant_id, chat_id, scoped_idempotency_key)
        if existing:
            return _msg_to_response(existing)

    # Save user message
    user_message = Message(
        tenant_id=tenant_id, chat_id=chat_id, role="user",
        content=content, idempotency_key=scoped_idempotency_key, status="sent",
    )
    db.add(user_message)
    await db.flush()
    await db.refresh(user_message)

    new_attachment_ids: list[str] = []
    if files:
        from app.services.storage import save_file, get_file_type
        from app.core.config import settings as _app_settings

        max_bytes = _app_settings.ATTACHMENT_MAX_FILE_MB * 1024 * 1024
        for upload_file in files:
            if not upload_file.filename:
                continue
            file_bytes = await upload_file.read()
            if not file_bytes:
                continue
            if len(file_bytes) > max_bytes:
                raise HTTPException(
                    status_code=413,
                    detail=f"File '{upload_file.filename}' exceeds {_app_settings.ATTACHMENT_MAX_FILE_MB}MB limit",
                )

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
            new_attachment_ids.append(str(att.id))

    # Reparent draft attachments — already processed via .../attachments/draft.
    if attachment_ids:
        draft_ids: list[uuid.UUID] = []
        for raw in attachment_ids.split(","):
            raw = raw.strip()
            if not raw:
                continue
            try:
                draft_ids.append(uuid.UUID(raw))
            except ValueError:
                raise HTTPException(status_code=400, detail=f"Invalid attachment_id: {raw}")
        if draft_ids:
            drafts = (await db.execute(
                select(MessageAttachment).where(
                    MessageAttachment.id.in_(draft_ids),
                    MessageAttachment.tenant_id == tenant_id,
                    MessageAttachment.chat_id == chat_id,
                    MessageAttachment.message_id.is_(None),
                )
            )).scalars().all()
            found = {str(d.id) for d in drafts}
            missing = [str(i) for i in draft_ids if str(i) not in found]
            if missing:
                raise HTTPException(
                    status_code=404,
                    detail=f"Draft attachment(s) not found or already attached: {', '.join(missing)}",
                )
            for d in drafts:
                d.message_id = user_message.id

    await db.commit()

    if new_attachment_ids:
        # Process attachments INLINE so the user gets a real answer in one
        # round trip. Background scheduling is a fallback for timeouts.
        from app.services.attachments.processor import process_attachment, process_attachment_background

        timeout_per_file = 90.0
        timed_out: list[str] = []
        for attachment_id in new_attachment_ids:
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

        # Anything that didn't finish gets pushed to background — the user can
        # ask again later and the result will be available.
        for attachment_id in timed_out:
            background_tasks.add_task(process_attachment_background, attachment_id, str(tenant_id))

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
        msg_status = "sent"
    except Exception as exc:
        assistant_content = f"Ошибка: {str(exc)[:500]}"
        prompt_tokens = completion_tokens = total_tokens = None
        latency_ms = None
        assistant_metadata = None
        msg_status = "error"

    assistant_message = Message(
        tenant_id=tenant_id, chat_id=chat_id, role="assistant",
        content=assistant_content, metadata_json=assistant_metadata, prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens, total_tokens=total_tokens,
        latency_ms=latency_ms, status=msg_status,
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


# ============================================================================
# Draft attachment uploads (admin mirror of tenant endpoints).
# See app/api/tenant/chats.py for the full workflow description.
# ============================================================================


async def _gc_stale_drafts_admin(db: AsyncSession, tenant_id: uuid.UUID, chat_id: uuid.UUID) -> None:
    from datetime import timedelta
    from app.core.config import settings as _app_settings
    from app.services.storage import delete_file
    try:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=_app_settings.ATTACHMENT_DRAFT_TTL_HOURS)
        stale = (await db.execute(
            select(MessageAttachment).where(
                MessageAttachment.tenant_id == tenant_id,
                MessageAttachment.chat_id == chat_id,
                MessageAttachment.message_id.is_(None),
                MessageAttachment.created_at < cutoff,
            )
        )).scalars().all()
        for d in stale:
            try:
                await delete_file(d.storage_path)
            except Exception:
                pass
            await db.delete(d)
        if stale:
            await db.commit()
    except Exception:
        logger.exception("[draft-gc] failed for chat=%s", chat_id)


@router.post(
    "/{chat_id}/attachments/draft",
    response_model=AttachmentBrief,
    status_code=status.HTTP_201_CREATED,
)
async def upload_draft_attachment_admin(
    tenant_id: uuid.UUID,
    chat_id: uuid.UUID,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    current_user: AdminUser = Depends(require_role("superadmin", "tenant_admin")),
):
    """Upload a single file as a draft (admin). Processing starts in background."""
    await _verify_tenant(tenant_id, db)

    chat_result = await db.execute(
        select(Chat).where(Chat.id == chat_id, Chat.tenant_id == tenant_id, Chat.deleted_at.is_(None))
    )
    if not chat_result.scalars().first():
        raise HTTPException(status_code=404, detail="Chat not found.")

    if not file.filename:
        raise HTTPException(status_code=400, detail="filename required")
    file_bytes = await file.read()
    if not file_bytes:
        raise HTTPException(status_code=400, detail="empty file")

    from app.core.config import settings as _app_settings
    max_bytes = _app_settings.ATTACHMENT_MAX_FILE_MB * 1024 * 1024
    if len(file_bytes) > max_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"File exceeds {_app_settings.ATTACHMENT_MAX_FILE_MB}MB limit",
        )

    await _gc_stale_drafts_admin(db, tenant_id, chat_id)

    from app.services.storage import save_file, get_file_type
    file_type = get_file_type(file.filename)
    storage_path = await save_file(str(tenant_id), str(chat_id), file.filename, file_bytes)

    att = MessageAttachment(
        message_id=None,
        tenant_id=tenant_id,
        chat_id=chat_id,
        filename=file.filename,
        file_type=file_type,
        file_size_bytes=len(file_bytes),
        storage_path=storage_path,
        processing_status="pending",
    )
    db.add(att)
    await db.commit()
    await db.refresh(att)

    from app.services.attachments.processor import process_attachment_background
    background_tasks.add_task(process_attachment_background, str(att.id), str(tenant_id))

    return AttachmentBrief(
        id=str(att.id),
        filename=att.filename,
        file_type=att.file_type,
        file_size_bytes=att.file_size_bytes,
        processing_status=att.processing_status,
        summary=att.summary,
    )


@router.get(
    "/{chat_id}/attachments/draft/{attachment_id}",
    response_model=AttachmentBrief,
)
async def get_draft_attachment_admin(
    tenant_id: uuid.UUID,
    chat_id: uuid.UUID,
    attachment_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: AdminUser = Depends(require_role("superadmin", "tenant_admin")),
):
    """Poll the processing status of a draft attachment (admin)."""
    await _verify_tenant(tenant_id, db)

    att = (await db.execute(
        select(MessageAttachment).where(
            MessageAttachment.id == attachment_id,
            MessageAttachment.tenant_id == tenant_id,
            MessageAttachment.chat_id == chat_id,
        )
    )).scalar_one_or_none()
    if not att:
        raise HTTPException(status_code=404, detail="Draft attachment not found.")

    return AttachmentBrief(
        id=str(att.id),
        filename=att.filename,
        file_type=att.file_type,
        file_size_bytes=att.file_size_bytes,
        processing_status=att.processing_status,
        summary=att.summary,
    )


@router.delete(
    "/{chat_id}/attachments/draft/{attachment_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_draft_attachment_admin(
    tenant_id: uuid.UUID,
    chat_id: uuid.UUID,
    attachment_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: AdminUser = Depends(require_role("superadmin", "tenant_admin")),
):
    """Cancel a draft upload (admin). Only unattached drafts can be deleted."""
    await _verify_tenant(tenant_id, db)

    att = (await db.execute(
        select(MessageAttachment).where(
            MessageAttachment.id == attachment_id,
            MessageAttachment.tenant_id == tenant_id,
            MessageAttachment.chat_id == chat_id,
            MessageAttachment.message_id.is_(None),
        )
    )).scalar_one_or_none()
    if not att:
        raise HTTPException(status_code=404, detail="Draft attachment not found or already attached.")

    from app.services.storage import delete_file
    try:
        await delete_file(att.storage_path)
    except Exception:
        logger.exception("draft delete: failed to remove file %s", att.storage_path)
    await db.delete(att)
    await db.commit()
    return None


# ============================================================================
# Artifacts (admin mirror of tenant endpoints).
# ============================================================================


def _admin_artifact_to_brief(a: Artifact) -> ArtifactBrief:
    return ArtifactBrief(
        id=str(a.id),
        chat_id=str(a.chat_id),
        source_message_id=str(a.source_message_id) if a.source_message_id else None,
        kind=a.kind,
        label=a.label,
        lang=a.lang,
        version=a.version,
        parent_artifact_id=str(a.parent_artifact_id) if a.parent_artifact_id else None,
        tokens_estimate=a.tokens_estimate,
        last_referenced_at=a.last_referenced_at,
        created_at=a.created_at,
    )


def _admin_artifact_to_detail(a: Artifact) -> ArtifactDetail:
    return ArtifactDetail(
        id=str(a.id),
        chat_id=str(a.chat_id),
        source_message_id=str(a.source_message_id) if a.source_message_id else None,
        kind=a.kind,
        label=a.label,
        lang=a.lang,
        version=a.version,
        parent_artifact_id=str(a.parent_artifact_id) if a.parent_artifact_id else None,
        tokens_estimate=a.tokens_estimate,
        last_referenced_at=a.last_referenced_at,
        created_at=a.created_at,
        content=a.content,
    )


@router.get("/{chat_id}/artifacts", response_model=list[ArtifactBrief])
async def list_artifacts_admin(
    tenant_id: uuid.UUID,
    chat_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: AdminUser = Depends(require_role("superadmin", "tenant_admin")),
):
    """List artifacts in this chat (admin)."""
    await _verify_tenant(tenant_id, db)
    rows = (await db.execute(
        select(Artifact)
        .where(
            Artifact.tenant_id == tenant_id,
            Artifact.chat_id == chat_id,
            Artifact.deleted_at.is_(None),
        )
        .order_by(Artifact.created_at.desc())
    )).scalars().all()
    return [_admin_artifact_to_brief(a) for a in rows]


@router.get("/{chat_id}/artifacts/{artifact_id}", response_model=ArtifactDetail)
async def get_artifact_admin(
    tenant_id: uuid.UUID,
    chat_id: uuid.UUID,
    artifact_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: AdminUser = Depends(require_role("superadmin", "tenant_admin")),
):
    """Fetch one artifact with full content (admin)."""
    await _verify_tenant(tenant_id, db)
    art = (await db.execute(
        select(Artifact).where(
            Artifact.id == artifact_id,
            Artifact.tenant_id == tenant_id,
            Artifact.chat_id == chat_id,
            Artifact.deleted_at.is_(None),
        )
    )).scalar_one_or_none()
    if not art:
        raise HTTPException(status_code=404, detail="Artifact not found.")
    return _admin_artifact_to_detail(art)
