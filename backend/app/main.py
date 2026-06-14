import logging
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from app.core.config import settings
from app.core.database import async_session, engine
from app.core.security import hash_password, verify_password
from app.models import AdminUser
from app.schemas.common import HealthResponse

logger = logging.getLogger(__name__)


def _enforce_secure_config() -> None:
    """Refuse to start with placeholder secrets — they sign JWTs and encrypt
    data-source credentials, so defaults make every tenant compromisable."""
    bad = [name for name in ("SECRET_KEY", "ENCRYPTION_KEY") if getattr(settings, name) == "change-me"]
    if bad:
        raise RuntimeError(
            f"{', '.join(bad)} is set to the default 'change-me'. "
            "Set real values in .env before starting the backend."
        )

from app.api.admin.auth import router as auth_router
from app.api.admin.key_groups import router as key_groups_router
from app.api.admin.tenants import router as tenants_router
from app.api.admin.keys import router as keys_router
from app.api.admin.shell_config import router as shell_router
from app.api.admin.tools import router as tools_router
from app.api.admin.builtin_tools import router as builtin_tools_router
from app.api.admin.data_sources import router as data_sources_router
from app.api.admin.kb import router as kb_router
from app.api.admin.memory import router as memory_router
from app.api.admin.chats import router as admin_chats_router
from app.api.admin.logs import router as logs_router
from app.api.admin.tier0 import router as tier0_router
from app.api.admin.retrieval_test import router as retrieval_test_router
from app.api.admin.assistants import router as assistants_router
from app.api.admin.tts_local import router as tts_local_router
from app.api.admin.stats import router as stats_router
from app.api.admin.audit import router as audit_router
from app.api.admin.models import router as models_router
from app.api.admin.model_config import router as model_config_router
from app.api.admin.custom_models import router as admin_custom_models_router
from app.api.admin.users import tenant_router as admin_users_tenant_router, global_router as admin_users_global_router
from app.api.admin.gpu import router as gpu_router
from app.api.tenant.chats import router as tenant_chats_router
from app.api.tenant.custom_models import router as tenant_custom_models_router
from app.api.tenant.voice import router as tenant_voice_router
from app.api.admin.voice import router as admin_voice_router
from app.api.tenant.pending_actions import router as tenant_pending_router
from app.api.admin.pending_actions import router as admin_pending_router


async def seed_admin():
    async with async_session() as db:
        from sqlalchemy import select
        existing = (await db.execute(select(AdminUser).where(AdminUser.login == settings.ADMIN_LOGIN))).scalar_one_or_none()
        if not existing:
            if settings.ADMIN_PASSWORD == "admin":
                raise RuntimeError(
                    "Refusing to seed the superadmin with the default password 'admin'. "
                    "Set ADMIN_PASSWORD in .env first."
                )
            admin = AdminUser(
                login=settings.ADMIN_LOGIN,
                password_hash=hash_password(settings.ADMIN_PASSWORD),
                role="superadmin",
            )
            db.add(admin)
            await db.commit()
        elif verify_password("admin", existing.password_hash):
            logger.critical(
                "Superadmin '%s' still uses the default password 'admin' — change it immediately.",
                settings.ADMIN_LOGIN,
            )


async def _warmup_fish_speech():
    """Fire a short TTS request for each reference voice so the model and
    voice-conditioning cache are hot before the first real user request.
    Runs in background — startup is not blocked."""
    import asyncio
    import logging
    import time

    logger = logging.getLogger("warmup.fish_speech")
    # Only needed when ElevenLabs is absent (Fish Speech is then the active TTS)
    if settings.ELEVENLABS_API_KEY:
        return

    try:
        import ormsgpack as _mp
    except ImportError:
        return

    async def _hit(ref_id: str, text: str):
        req = {
            "text": text,
            "reference_id": ref_id,
            "format": "mp3",
            "normalize": True,
            "streaming": False,
            "use_memory_cache": "on",
        }
        body = _mp.packb(req)
        t0 = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                r = await client.post(
                    f"{settings.TTS_URL}/v1/tts",
                    content=body,
                    headers={"Content-Type": "application/msgpack"},
                )
                logger.info("Fish Speech warm-up [%s]: HTTP %s, %.2fs, %d B",
                            ref_id, r.status_code, time.monotonic() - t0, len(r.content))
        except Exception as exc:
            logger.warning("Fish Speech warm-up [%s] failed (non-fatal): %s", ref_id, exc)

    await asyncio.gather(
        _hit("ru", "Система голосового помощника готова."),
        _hit("uk", "Система голосового помощника готова."),
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    import asyncio
    from app.services.gpu_metrics import snapshot_worker

    from app.services.jobs.queue import run_worker

    _enforce_secure_config()
    await seed_admin()
    gpu_task = asyncio.create_task(snapshot_worker())
    asyncio.create_task(_warmup_fish_speech())
    jobs_stop = asyncio.Event()
    jobs_task = asyncio.create_task(run_worker(jobs_stop))
    try:
        yield
    finally:
        jobs_stop.set()
        gpu_task.cancel()
        for _t in (gpu_task, jobs_task):
            try:
                await _t
            except asyncio.CancelledError:
                pass
        await engine.dispose()


app = FastAPI(title="Multi-tenant AI Platform", version="1.0.0", lifespan=lifespan)


@app.middleware("http")
async def security_headers(request, call_next):
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    return response


app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Admin routes
app.include_router(auth_router)
app.include_router(tenants_router)
app.include_router(key_groups_router)
app.include_router(keys_router)
app.include_router(shell_router)
app.include_router(tools_router)
app.include_router(builtin_tools_router)
app.include_router(data_sources_router)
app.include_router(kb_router)
app.include_router(memory_router)
app.include_router(admin_chats_router)
app.include_router(logs_router)
app.include_router(tier0_router)
app.include_router(retrieval_test_router)
app.include_router(assistants_router)
app.include_router(tts_local_router)
app.include_router(stats_router)
app.include_router(audit_router)
app.include_router(models_router)
app.include_router(model_config_router)
app.include_router(admin_custom_models_router)
app.include_router(admin_users_tenant_router)
app.include_router(admin_users_global_router)
app.include_router(gpu_router)

# Tenant API routes
app.include_router(tenant_chats_router)
app.include_router(tenant_custom_models_router)
app.include_router(tenant_voice_router)
app.include_router(admin_voice_router)
app.include_router(tenant_pending_router)
app.include_router(admin_pending_router)


@app.get("/health", response_model=HealthResponse)
async def health():
    db_status = "ok"
    try:
        async with async_session() as db:
            await db.execute(text("SELECT 1"))
    except Exception:
        db_status = "error"

    ollama_status = None
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.get(f"{settings.OLLAMA_BASE_URL}/")
            ollama_status = "ok" if r.status_code == 200 else "error"
    except Exception:
        ollama_status = "unavailable"

    status = "ok" if db_status == "ok" else "degraded"
    return HealthResponse(status=status, database=db_status, ollama=ollama_status)


@app.get("/ready")
async def ready():
    async with async_session() as db:
        await db.execute(text("SELECT 1"))
    return {"status": "ready"}
