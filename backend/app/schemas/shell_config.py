from pydantic import BaseModel


class ShellConfigUpdate(BaseModel):
    provider_type: str | None = None
    provider_base_url: str | None = None
    provider_api_key: str | None = None
    model_name: str | None = None
    system_prompt: str | None = None
    ontology_prompt: str | None = None
    rules_text: str | None = None
    temperature: float | None = None
    max_context_messages: int | None = None
    max_tokens: int | None = None
    summary_model_name: str | None = None
    context_mode: str | None = None
    memory_enabled: bool | None = None
    knowledge_base_enabled: bool | None = None
    embedding_model_name: str | None = None
    vision_model_name: str | None = None
    kb_max_chunks: int | None = None
    tools_policy: str | None = None
    enable_thinking: str | None = None
    response_language: str | None = None


class ShellConfigResponse(BaseModel):
    id: str
    tenant_id: str
    provider_type: str
    provider_base_url: str | None
    provider_api_key_masked: str | None = None
    model_name: str
    system_prompt: str | None
    ontology_prompt: str | None = None
    rules_text: str | None
    temperature: float
    max_context_messages: int
    max_tokens: int
    summary_model_name: str | None
    context_mode: str
    memory_enabled: bool
    knowledge_base_enabled: bool
    embedding_model_name: str | None
    vision_model_name: str | None = None
    kb_max_chunks: int
    tools_policy: str
    enable_thinking: str
    response_language: str = "ru"

    model_config = {"from_attributes": True}


class TestConnectionRequest(BaseModel):
    provider_type: str
    provider_base_url: str | None = None
    provider_api_key: str | None = None
    model_name: str | None = None


class TestConnectionResponse(BaseModel):
    success: bool
    message: str
    models: list[str] | None = None
