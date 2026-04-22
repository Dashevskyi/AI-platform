from datetime import datetime
from pydantic import BaseModel


class KBDocumentCreate(BaseModel):
    title: str
    doc_type: str = "text"  # text, url, file
    source_type: str = "manual"  # manual, faq, solution, procedure, reference
    source_url: str | None = None
    content: str = ""
    metadata_json: dict | None = None
    is_active: bool = True


class KBDocumentUpdate(BaseModel):
    title: str | None = None
    source_type: str | None = None
    content: str | None = None
    metadata_json: dict | None = None
    is_active: bool | None = None


class KBDocumentResponse(BaseModel):
    id: str
    tenant_id: str
    title: str
    doc_type: str
    source_type: str
    source_url: str | None
    source_filename: str | None
    content: str
    metadata_json: dict | None
    is_active: bool
    embedding_status: str
    embedding_error: str | None
    chunks_count: int
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class KBChunkResponse(BaseModel):
    id: str
    document_id: str
    chunk_index: int
    content: str
    doc_title: str
    source_type: str
    source_url: str | None

    model_config = {"from_attributes": True}
