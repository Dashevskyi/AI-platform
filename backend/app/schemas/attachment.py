from pydantic import BaseModel
from datetime import datetime


class AttachmentResponse(BaseModel):
    id: str
    message_id: str | None
    tenant_id: str
    chat_id: str
    filename: str
    file_type: str
    file_size_bytes: int
    processing_status: str
    processing_error: str | None
    summary: str | None
    chunks_count: int
    created_at: datetime

    model_config = {"from_attributes": True}


class AttachmentBrief(BaseModel):
    id: str
    filename: str
    file_type: str
    file_size_bytes: int
    processing_status: str
    summary: str | None

    model_config = {"from_attributes": True}
