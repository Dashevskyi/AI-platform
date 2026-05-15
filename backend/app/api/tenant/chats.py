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
from app.models.artifact import Artifact
from app.schemas.chat import ChatCreate, ChatResponse, MessageSend, MessageResponse, PublicMessageResponse
from app.schemas.attachment import AttachmentBrief
from app.schemas.artifact import ArtifactBrief, ArtifactDetail
from app.schemas.common import PaginatedResponse
from app.api.deps import TenantAuthContext, get_current_tenant_auth_context

router = APIRouter(
    prefix="/api/tenants/{tenant_id}/chats",
    tags=["tenant-chats"],
)


def _artifact_to_brief(a: Artifact) -> ArtifactBrief:
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


def _artifact_to_detail(a: Artifact) -> ArtifactDetail:
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
        existing = await _find_idempotent_response(db, tenant_id, chat_id, scoped_idempotency_key)
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


def _build_assistant_metadata(llm_result: dict | None) -> dict | None:
    if not llm_result:
        return None
    return {
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

    async def _save_assistant(content: str, status_: str = "sent",
                              llm_result: dict | None = None) -> str:
        """Persist the assistant message in a fresh DB session and return its id.
        Also schedules a background resume-generation task for the (user, assistant) pair."""
        from app.core.database import async_session as _save_session
        assistant_metadata = _build_assistant_metadata(llm_result)
        async with _save_session() as save_db:
            msg = Message(
                tenant_id=tenant_id, chat_id=chat_id, role="assistant",
                content=content,
                metadata_json=assistant_metadata,
                prompt_tokens=(llm_result or {}).get("prompt_tokens"),
                completion_tokens=(llm_result or {}).get("completion_tokens"),
                total_tokens=(llm_result or {}).get("total_tokens"),
                latency_ms=(llm_result or {}).get("latency_ms"),
                status=status_,
            )
            save_db.add(msg)
            await save_db.flush()
            await save_db.commit()
            await save_db.refresh(msg)
            new_id = str(msg.id)

        # Schedule resume generation in background (best-effort, non-blocking).
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
                logger.exception("[tenant-stream] failed to schedule resume generation")
        return new_id

    async def runner() -> None:
        """
        Run pipeline, save assistant message, push 'final' event.
        IMPORTANT: saving happens here (not in event_gen) so that if the client
        disconnects mid-stream the result is still persisted to DB.
        """
        from app.services.llm.pipeline import chat_completion
        from app.services.throttle import ThrottleRejected
        from app.services.message_merger import submit_or_merge
        from app.core.database import async_session
        try:
            final_id: str | None = None
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
                # Merger saves the message internally — just relay the id
                final_id = merged_result["assistant_message_id"]
            else:
                async with async_session() as fresh_db:
                    result = await chat_completion(
                        tenant_id=str(tenant_id), chat_id=str(chat_id),
                        user_content=body.content, db=fresh_db,
                        user_message_id=user_message_id,
                        api_key_id=api_key_id_str,
                        on_event=emitter,
                    )
                    await fresh_db.commit()
                final_id = await _save_assistant(result.get("content", ""), llm_result=result)
            await queue.put(("final", {"assistant_message_id": final_id}))
            await queue.put(None)
        except ThrottleRejected as exc:
            await queue.put(("throttle_rejected", {"message": str(exc), "retry_after": exc.retry_after}))
            try:
                msg_id = await _save_assistant("Превышен лимит запросов. Попробуйте позже.", status_="error")
                await queue.put(("final", {"assistant_message_id": msg_id}))
            except Exception:
                logger.exception("[tenant-stream] failed to save throttle-rejected message")
            await queue.put(None)
        except Exception as exc:
            logger.exception("[tenant-stream] pipeline runner failed for tenant=%s chat=%s", tenant_id, chat_id)
            await queue.put(("error", {"message": str(exc)[:500]}))
            try:
                msg_id = await _save_assistant("Ошибка обработки запроса.", status_="error")
                await queue.put(("final", {"assistant_message_id": msg_id}))
            except Exception:
                logger.exception("[tenant-stream] failed to save error message")
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
            # Client disconnected — let pipeline_task finish and save its result
            # in the background. DO NOT cancel it (otherwise message is lost).
            logger.info("[tenant-stream] client disconnected; pipeline continues in background")
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
    # Comma-separated list of draft attachment UUIDs already uploaded via
    # POST .../attachments/draft. They get reparented to the new user message
    # without re-processing — summaries are already there.
    attachment_ids: Optional[str] = Form(default=None),
    db: AsyncSession = Depends(get_db),
    auth: TenantAuthContext = Depends(get_current_tenant_auth_context),
):
    """Send a message with file attachments via tenant API.

    Two ways to attach files:
    - Raw files via `files`: uploaded + processed inline (legacy path).
    - Pre-uploaded drafts via `attachment_ids`: client already POSTed them to
      /attachments/draft and (optionally) polled them to processing_status=done.
    Both can be combined in one request.
    """
    _verify_tenant_access(tenant_id, auth.tenant)

    chat_result = await db.execute(
        _chat_scope_query(tenant_id, auth.api_key.id).where(Chat.id == chat_id)
    )
    if not chat_result.scalars().first():
        raise HTTPException(status_code=404, detail="Chat not found.")

    scoped_idempotency_key = _build_scoped_idempotency_key(tenant_id, chat_id, idempotency_key)
    if scoped_idempotency_key:
        existing = await _find_idempotent_response(db, tenant_id, chat_id, scoped_idempotency_key)
        if existing:
            return _msg_to_response(existing)

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

    # Reparent draft attachments (no re-processing — summary is already there).
    draft_ids_to_reparent: list[uuid.UUID] = []
    if attachment_ids:
        for raw in attachment_ids.split(","):
            raw = raw.strip()
            if not raw:
                continue
            try:
                draft_ids_to_reparent.append(uuid.UUID(raw))
            except ValueError:
                raise HTTPException(status_code=400, detail=f"Invalid attachment_id: {raw}")
        if draft_ids_to_reparent:
            drafts = (await db.execute(
                select(MessageAttachment).where(
                    MessageAttachment.id.in_(draft_ids_to_reparent),
                    MessageAttachment.tenant_id == tenant_id,
                    MessageAttachment.chat_id == chat_id,
                    MessageAttachment.message_id.is_(None),
                )
            )).scalars().all()
            found_ids = {str(d.id) for d in drafts}
            missing = [str(i) for i in draft_ids_to_reparent if str(i) not in found_ids]
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
        assistant_metadata = _build_assistant_metadata(llm_result)
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


# ============================================================================
# Draft attachment uploads — let the UI start processing files BEFORE the user
# hits "send". Workflow:
#   1) POST .../attachments/draft  (single file) → returns {id, processing_status="pending"}
#   2) Backend processes in background (OCR / summary / chunks)
#   3) UI polls GET .../attachments/draft/{id} until status="done" or "error"
#   4) On submit, client passes ids in `attachment_ids` Form field of /messages/upload
#   5) Drafts get reparented to the new user message (no re-processing)
# Unsent drafts older than ATTACHMENT_DRAFT_TTL_HOURS are GC'd lazily on POST.
# ============================================================================


async def _gc_stale_drafts(db: AsyncSession, tenant_id: uuid.UUID, chat_id: uuid.UUID) -> None:
    """Best-effort cleanup of unattached drafts older than TTL. Lazy — runs on
    each new draft upload. Failure is logged and swallowed (don't block uploads)."""
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
async def upload_draft_attachment(
    tenant_id: uuid.UUID,
    chat_id: uuid.UUID,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    auth: TenantAuthContext = Depends(get_current_tenant_auth_context),
):
    """Upload a single file as a draft (not yet bound to a message). Processing
    starts immediately in background. Poll the GET endpoint for status; then
    pass the returned id in `attachment_ids` of /messages/upload."""
    _verify_tenant_access(tenant_id, auth.tenant)

    chat_result = await db.execute(
        _chat_scope_query(tenant_id, auth.api_key.id).where(Chat.id == chat_id)
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

    # Lazy GC of stale drafts in this chat — keeps storage bounded.
    await _gc_stale_drafts(db, tenant_id, chat_id)

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

    # Kick off processing in background — the client will poll for completion.
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
async def get_draft_attachment(
    tenant_id: uuid.UUID,
    chat_id: uuid.UUID,
    attachment_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    auth: TenantAuthContext = Depends(get_current_tenant_auth_context),
):
    """Poll the processing status of a draft attachment. Returns processing_status
    in {pending, processing, done, error}, plus summary when done."""
    _verify_tenant_access(tenant_id, auth.tenant)

    chat_result = await db.execute(
        _chat_scope_query(tenant_id, auth.api_key.id).where(Chat.id == chat_id)
    )
    if not chat_result.scalars().first():
        raise HTTPException(status_code=404, detail="Chat not found.")

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
async def delete_draft_attachment(
    tenant_id: uuid.UUID,
    chat_id: uuid.UUID,
    attachment_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    auth: TenantAuthContext = Depends(get_current_tenant_auth_context),
):
    """Cancel a draft upload. Only unattached drafts (message_id IS NULL) can be
    deleted via this endpoint — attached files belong to their message and stay."""
    _verify_tenant_access(tenant_id, auth.tenant)

    chat_result = await db.execute(
        _chat_scope_query(tenant_id, auth.api_key.id).where(Chat.id == chat_id)
    )
    if not chat_result.scalars().first():
        raise HTTPException(status_code=404, detail="Chat not found.")

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
# Artifacts — first-class entities (scripts, configs, SQL, ...) extracted from
# chat messages. Read-only from the tenant side: artifacts are produced by
# the LLM pipeline, not authored by API clients.
# ============================================================================


@router.get("/{chat_id}/artifacts", response_model=list[ArtifactBrief])
async def list_artifacts(
    tenant_id: uuid.UUID,
    chat_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    auth: TenantAuthContext = Depends(get_current_tenant_auth_context),
):
    """List artifacts in this chat (briefs without content). Latest first."""
    _verify_tenant_access(tenant_id, auth.tenant)
    chat_result = await db.execute(
        _chat_scope_query(tenant_id, auth.api_key.id).where(Chat.id == chat_id)
    )
    if not chat_result.scalars().first():
        raise HTTPException(status_code=404, detail="Chat not found.")
    rows = (await db.execute(
        select(Artifact)
        .where(
            Artifact.tenant_id == tenant_id,
            Artifact.chat_id == chat_id,
            Artifact.deleted_at.is_(None),
        )
        .order_by(Artifact.created_at.desc())
    )).scalars().all()
    return [_artifact_to_brief(a) for a in rows]


@router.get("/{chat_id}/artifacts/{artifact_id}", response_model=ArtifactDetail)
async def get_artifact(
    tenant_id: uuid.UUID,
    chat_id: uuid.UUID,
    artifact_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    auth: TenantAuthContext = Depends(get_current_tenant_auth_context),
):
    """Fetch one artifact with full content."""
    _verify_tenant_access(tenant_id, auth.tenant)
    chat_result = await db.execute(
        _chat_scope_query(tenant_id, auth.api_key.id).where(Chat.id == chat_id)
    )
    if not chat_result.scalars().first():
        raise HTTPException(status_code=404, detail="Chat not found.")
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
    return _artifact_to_detail(art)
