from datetime import datetime
from pydantic import BaseModel


class MemoryCreate(BaseModel):
    chat_id: str | None = None
    memory_type: str = "long_term"
    content: str
    metadata_json: dict | None = None
    priority: int = 0
    is_pinned: bool = False
    expires_at: datetime | None = None


class MemoryUpdate(BaseModel):
    memory_type: str | None = None
    content: str | None = None
    metadata_json: dict | None = None
    priority: int | None = None
    is_pinned: bool | None = None
    expires_at: datetime | None = None


class MemoryResponse(BaseModel):
    id: str
    tenant_id: str
    chat_id: str | None
    memory_type: str
    content: str
    metadata_json: dict | None
    priority: int
    is_pinned: bool
    expires_at: datetime | None
    created_at: datetime

    model_config = {"from_attributes": True}
