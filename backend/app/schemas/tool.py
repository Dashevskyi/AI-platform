from datetime import datetime
from pydantic import BaseModel


class ToolCreate(BaseModel):
    name: str
    description: str | None = None
    group: str | None = None
    config_json: dict | None = None
    tool_type: str = "function"
    is_active: bool = True
    is_pinned: bool = False


class ToolUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    group: str | None = None
    config_json: dict | None = None
    tool_type: str | None = None
    is_active: bool | None = None
    is_pinned: bool | None = None


class ToolResponse(BaseModel):
    id: str
    tenant_id: str
    name: str
    description: str | None
    group: str | None
    config_json: dict | None
    tool_type: str
    is_active: bool
    is_pinned: bool = False
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ToolTestRequest(BaseModel):
    config_json: dict
    arguments: dict | None = None


class ToolTestResponse(BaseModel):
    success: bool
    output: str
    error: str | None = None
