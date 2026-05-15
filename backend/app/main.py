from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from app.core.config import settings
from app.core.database import async_session, engine
from app.core.security import hash_password
from app.models import AdminUser
from app.schemas.common import HealthResponse

from app.api.admin.auth import router as auth_router
from app.api.admin.key_groups import router as key_groups_router
from app.api.admin.tenants import router as tenants_router
from app.api.admin.keys import router as keys_router
from app.api.admin.shell_config import router as shell_router
from app.api.admin.tools import router as tools_router
from app.api.admin.data_sources import router as data_sources_router
from app.api.admin.kb import router as kb_router
from app.api.admin.memory import router as memory_router
from app.api.admin.chats import router as admin_chats_router
from app.api.admin.logs import router as logs_router
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


async def seed_admin():
    async with async_session() as db:
        from sqlalchemy import select
        existing = (await db.execute(select(AdminUser).where(AdminUser.login == settings.ADMIN_LOGIN))).scalar_one_or_none()
        if not existing:
            admin = AdminUser(
                login=settings.ADMIN_LOGIN,
                password_hash=hash_password(settings.ADMIN_PASSWORD),
                role="superadmin",
            )
            db.add(admin)
            await db.commit()


@asynccontextmanager
async def lifespan(app: FastAPI):
    import asyncio
    from app.services.gpu_metrics import snapshot_worker

    await seed_admin()
    gpu_task = asyncio.create_task(snapshot_worker())
    try:
        yield
    finally:
        gpu_task.cancel()
        try:
            await gpu_task
        except asyncio.CancelledError:
            pass
        await engine.dispose()


app = FastAPI(title="Multi-tenant AI Platform", version="1.0.0", lifespan=lifespan)

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
app.include_router(data_sources_router)
app.include_router(kb_router)
app.include_router(memory_router)
app.include_router(admin_chats_router)
app.include_router(logs_router)
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
