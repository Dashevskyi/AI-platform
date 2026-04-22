from pydantic import BaseModel
from datetime import datetime


class LLMModelCreate(BaseModel):
    name: str
    provider_type: str
    base_url: str | None = None
    api_key: str | None = None
    model_id: str
    tier: str = "medium"
    supports_tools: bool = False
    supports_vision: bool = False
    max_context_tokens: int | None = None
    cost_per_1k_input: float | None = None
    cost_per_1k_output: float | None = None
    is_active: bool = True


class LLMModelUpdate(BaseModel):
    name: str | None = None
    provider_type: str | None = None
    base_url: str | None = None
    api_key: str | None = None
    model_id: str | None = None
    tier: str | None = None
    supports_tools: bool | None = None
    supports_vision: bool | None = None
    max_context_tokens: int | None = None
    cost_per_1k_input: float | None = None
    cost_per_1k_output: float | None = None
    is_active: bool | None = None


class LLMModelResponse(BaseModel):
    id: str
    name: str
    provider_type: str
    base_url: str | None
    api_key_masked: str | None = None
    model_id: str
    tier: str
    supports_tools: bool
    supports_vision: bool
    max_context_tokens: int | None
    cost_per_1k_input: float | None
    cost_per_1k_output: float | None
    is_active: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class LLMModelBrief(BaseModel):
    """Short model info for selection dropdowns."""
    id: str
    name: str
    provider_type: str
    model_id: str
    tier: str
    supports_tools: bool
    supports_vision: bool

    model_config = {"from_attributes": True}


class TenantCustomModelCreate(BaseModel):
    name: str
    provider_type: str
    base_url: str | None = None
    api_key: str | None = None
    model_id: str
    tier: str = "medium"
    supports_tools: bool = False
    supports_vision: bool = False
    max_context_tokens: int | None = None


class TenantCustomModelUpdate(BaseModel):
    name: str | None = None
    provider_type: str | None = None
    base_url: str | None = None
    api_key: str | None = None
    model_id: str | None = None
    tier: str | None = None
    supports_tools: bool | None = None
    supports_vision: bool | None = None
    max_context_tokens: int | None = None
    is_active: bool | None = None


class TenantCustomModelResponse(BaseModel):
    id: str
    tenant_id: str
    name: str
    provider_type: str
    base_url: str | None
    api_key_masked: str | None = None
    model_id: str
    tier: str
    supports_tools: bool
    supports_vision: bool
    max_context_tokens: int | None
    is_active: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class TenantModelConfigUpdate(BaseModel):
    mode: str | None = None
    manual_model_id: str | None = None
    manual_custom_model_id: str | None = None
    auto_light_model_id: str | None = None
    auto_heavy_model_id: str | None = None
    auto_light_custom_model_id: str | None = None
    auto_heavy_custom_model_id: str | None = None
    complexity_threshold: float | None = None


class TenantModelConfigResponse(BaseModel):
    id: str
    tenant_id: str
    mode: str
    manual_model_id: str | None
    manual_custom_model_id: str | None
    auto_light_model_id: str | None
    auto_heavy_model_id: str | None
    auto_light_custom_model_id: str | None
    auto_heavy_custom_model_id: str | None
    complexity_threshold: float
    # Resolved display names for frontend
    manual_model_name: str | None = None
    manual_custom_model_name: str | None = None
    auto_light_model_name: str | None = None
    auto_heavy_model_name: str | None = None
    auto_light_custom_model_name: str | None = None
    auto_heavy_custom_model_name: str | None = None

    model_config = {"from_attributes": True}


class TestModelConnectionRequest(BaseModel):
    provider_type: str
    base_url: str | None = None
    api_key: str | None = None
    model_id: str | None = None


class TestModelConnectionResponse(BaseModel):
    success: bool
    message: str
    models: list[str] | None = None
