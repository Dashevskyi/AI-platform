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
    throttle_enabled: bool | None = None
    throttle_max_concurrent: int | None = None
    throttle_overflow_policy: str | None = None
    throttle_queue_max: int | None = None
    merge_messages_enabled: bool | None = None
    merge_window_ms: int | None = None


class TenantResponse(BaseModel):
    id: str
    name: str
    slug: str
    description: str | None
    is_active: bool
    throttle_enabled: bool = False
    throttle_max_concurrent: int = 5
    throttle_overflow_policy: str = "reject_429"
    throttle_queue_max: int = 20
    merge_messages_enabled: bool = False
    merge_window_ms: int = 1500
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class TenantApiKeyCreate(BaseModel):
    name: str
    expires_at: datetime | None = None
    group_id: str | None = None
    memory_prompt: str | None = None
    allowed_tool_ids: list[str] | None = None


class TenantApiKeyUpdate(BaseModel):
    name: str | None = None
    expires_at: datetime | None = None
    is_active: bool | None = None
    group_id: str | None = None
    memory_prompt: str | None = None
    allowed_tool_ids: list[str] | None = None


class TenantApiKeyGroupCreate(BaseModel):
    name: str
    memory_prompt: str | None = None
    allowed_tool_ids: list[str] | None = None


class TenantApiKeyGroupUpdate(BaseModel):
    name: str | None = None
    memory_prompt: str | None = None
    allowed_tool_ids: list[str] | None = None


class TenantApiKeyGroupResponse(BaseModel):
    id: str
    tenant_id: str
    name: str
    memory_prompt: str | None
    allowed_tool_ids: list[str] | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class TenantApiKeyResponse(BaseModel):
    id: str
    tenant_id: str
    name: str
    key_prefix: str
    group_id: str | None
    group_name: str | None
    memory_prompt: str | None
    allowed_tool_ids: list[str] | None
    is_active: bool
    expires_at: datetime | None
    last_used_at: datetime | None
    created_at: datetime

    model_config = {"from_attributes": True}


class TenantApiKeyCreated(TenantApiKeyResponse):
    raw_key: str
