from datetime import datetime
from pydantic import BaseModel


class TenantCreate(BaseModel):
    name: str
    slug: str
    description: str | None = None
    is_active: bool = True


class TenantUpdate(BaseModel):
    name: str | None = None
    slug: str | None = None
    description: str | None = None
    is_active: bool | None = None


class TenantResponse(BaseModel):
    id: str
    name: str
    slug: str
    description: str | None
    is_active: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class TenantApiKeyCreate(BaseModel):
    name: str
    expires_at: datetime | None = None


class TenantApiKeyResponse(BaseModel):
    id: str
    tenant_id: str
    name: str
    key_prefix: str
    is_active: bool
    expires_at: datetime | None
    last_used_at: datetime | None
    created_at: datetime

    model_config = {"from_attributes": True}


class TenantApiKeyCreated(TenantApiKeyResponse):
    raw_key: str
