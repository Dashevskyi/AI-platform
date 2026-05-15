from datetime import datetime

from pydantic import BaseModel


class TenantDataSourceCreate(BaseModel):
    name: str
    description: str | None = None
    kind: str
    config_json: dict | None = None
    secret_json: dict | None = None
    is_active: bool = True


class TenantDataSourceUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    kind: str | None = None
    config_json: dict | None = None
    secret_json: dict | None = None
    is_active: bool | None = None


class TenantDataSourceResponse(BaseModel):
    id: str
    tenant_id: str
    name: str
    description: str | None
    kind: str
    config_json: dict | None
    secret_json_masked: dict | None
    is_active: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
