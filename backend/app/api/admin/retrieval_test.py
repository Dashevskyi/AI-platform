"""Admin diagnostic bench for semantic retrieval.

Runs the SAME builtin tools the model uses — search_kb, recall_chat,
recall_memory, find_artifacts — against a test query and returns each tool's
raw output verbatim. The point is to see EXACTLY what the model receives when
it reaches for a retrieval tool, so an admin can judge whether the KB /
memory / history indexes return the right things for a given phrasing.

Mirrors the Tier 0 test bench (api/admin/tier0.py): same auth, same
per-tenant prefix, scratch-free (these tools are read-only).
"""
import logging
import time
import uuid

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.api.deps import require_role, require_tenant_access, require_permission
from app.models.tenant_shell_config import TenantShellConfig

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/admin/tenants/{tenant_id}/retrieval",
    tags=["admin-retrieval"],
    dependencies=[
        Depends(require_role("superadmin", "tenant_admin")),
        Depends(require_tenant_access),
        Depends(require_permission("logs")),
    ],
)

# source key → (builtin tool name, supports a chat/tenant scope arg)
_SOURCES: dict[str, tuple[str, bool]] = {
    "kb": ("search_kb", False),
    "memory": ("recall_memory", True),
    "chat": ("recall_chat", True),
    "artifacts": ("find_artifacts", True),
}


class RetrievalTestRequest(BaseModel):
    query: str
    # Which sources to probe; default all.
    sources: list[str] | None = None
    # Optional chat to scope chat/memory/artifacts recall to a single thread.
    # When None, scope falls back to "tenant" (cross-chat) where supported.
    chat_id: str | None = None
    limit: int = 5


class RetrievalSourceResult(BaseModel):
    source: str
    tool: str
    scope: str
    success: bool
    output: str
    error: str | None = None
    latency_ms: int


class RetrievalTestResponse(BaseModel):
    query: str
    embedding_model: str | None
    recall_cross_chat_enabled: bool
    results: list[RetrievalSourceResult]


@router.post("/test", response_model=RetrievalTestResponse)
async def retrieval_test(
    tenant_id: uuid.UUID,
    body: RetrievalTestRequest,
    db: AsyncSession = Depends(get_db),
) -> RetrievalTestResponse:
    """Probe one or more retrieval indexes with a query, returning each tool's
    raw output — the exact text the model would get from that tool call."""
    from app.services.tools.executor import execute_tool

    query = (body.query or "").strip()
    cfg = (
        await db.execute(
            select(TenantShellConfig).where(TenantShellConfig.tenant_id == tenant_id)
        )
    ).scalar_one_or_none()
    cross_chat = bool(getattr(cfg, "recall_cross_chat_enabled", False)) if cfg else False

    wanted = body.sources or list(_SOURCES.keys())
    chat_id = (body.chat_id or "").strip() or None
    limit = max(1, min(int(body.limit or 5), 20))

    tool_config = {
        "_context": {
            "tenant_id": str(tenant_id),
            "chat_id": chat_id,
            "api_key_id": None,
            "timezone": (getattr(cfg, "timezone", None) or None) if cfg else None,
        }
    }

    results: list[RetrievalSourceResult] = []
    for source in wanted:
        spec = _SOURCES.get(source)
        if not spec:
            continue
        tool_name, scoped = spec
        scope = ("chat" if chat_id else "tenant") if scoped else "—"
        args: dict = {"query": query, "limit": limit}
        if scoped:
            args["scope"] = "chat" if chat_id else "tenant"

        t0 = time.perf_counter()
        if not query:
            res_success, res_output, res_error = False, "", "query is empty"
        else:
            try:
                r = await execute_tool(tool_name, args, tool_config)
                res_success, res_output, res_error = r.success, r.output or "", r.error
            except Exception as e:  # defensive — a tool crash shouldn't 500 the bench
                logger.exception("retrieval_test: %s failed", tool_name)
                res_success, res_output, res_error = False, "", str(e)[:300]
        latency_ms = int((time.perf_counter() - t0) * 1000)

        results.append(
            RetrievalSourceResult(
                source=source,
                tool=tool_name,
                scope=scope,
                success=res_success,
                output=res_output,
                error=res_error,
                latency_ms=latency_ms,
            )
        )

    return RetrievalTestResponse(
        query=query,
        embedding_model=getattr(cfg, "embedding_model_name", None) if cfg else None,
        recall_cross_chat_enabled=cross_chat,
        results=results,
    )
