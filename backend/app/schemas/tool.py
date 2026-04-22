from datetime import datetime
from pydantic import BaseModel


class ToolCreate(BaseModel):
    name: str
    description: str | None = None
    group: str | None = None
    config_json: dict | None = None
    tool_type: str = "function"
    is_active: bool = True


class ToolUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    group: str | None = None
    config_json: dict | None = None
    tool_type: str | None = None
    is_active: bool | None = None


class ToolResponse(BaseModel):
    id: str
    tenant_id: str
    name: str
    description: str | None
    group: str | None
    config_json: dict | None
    tool_type: str
    is_active: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
