from datetime import datetime
from pydantic import BaseModel


class ChatCreate(BaseModel):
    title: str | None = None
    description: str | None = None


class ChatUpdate(BaseModel):
    title: str | None = None
    description: str | None = None
    status: str | None = None


class ChatResponse(BaseModel):
    id: str
    tenant_id: str
    api_key_id: str | None = None
    title: str | None
    description: str | None
    status: str
    created_by: str | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class MessageSend(BaseModel):
    content: str
    idempotency_key: str | None = None


class MessageResponse(BaseModel):
    id: str
    tenant_id: str
    chat_id: str
    role: str
    content: str
    metadata_json: dict | None = None
    prompt_tokens: int | None
    completion_tokens: int | None
    total_tokens: int | None
    latency_ms: float | None
    time_to_first_token_ms: float | None = None
    provider_type: str | None = None
    model_name: str | None = None
    correlation_id: str | None = None
    tool_calls_count: int | None = None
    finish_reason: str | None = None
    status: str
    created_at: datetime

    model_config = {"from_attributes": True}


class PublicMessageResponse(BaseModel):
    """End-user-facing message shape for the tenant API.

    Strips internal metadata: no events trail, no reasoning, no tool/provider
    details, no token counts, no model name. End clients (CRMs, embedded
    chat widgets) should not see these.
    """
    id: str
    chat_id: str
    role: str
    content: str
    status: str
    created_at: datetime

    model_config = {"from_attributes": True}
