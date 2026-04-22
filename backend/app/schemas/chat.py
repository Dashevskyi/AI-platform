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
    prompt_tokens: int | None
    completion_tokens: int | None
    total_tokens: int | None
    latency_ms: float | None
    status: str
    created_at: datetime

    model_config = {"from_attributes": True}
