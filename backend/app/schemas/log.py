from datetime import datetime
from pydantic import BaseModel


class LLMLogResponse(BaseModel):
    id: str
    tenant_id: str
    chat_id: str | None
    api_key_id: str | None
    message_id: str | None
    correlation_id: str | None
    provider_type: str
    model_name: str
    status: str
    error_text: str | None
    latency_ms: float | None
    time_to_first_token_ms: float | None
    prompt_tokens: int | None
    completion_tokens: int | None
    total_tokens: int | None
    tool_calls_count: int | None
    tool_errors_count: int | None = None  # failed tool calls within this request
    finish_reason: str | None
    estimated_cost: float | None
    served_by: str | None = None  # 'tier0_template' | 'llm'
    request_preview: str | None = None  # short snippet of the user's prompt
    created_at: datetime

    model_config = {"from_attributes": True}


class LLMLogSummary(BaseModel):
    """Aggregates over the currently-filtered logs — shown above the table."""
    total: int
    errors: int
    error_rate: float
    avg_latency_ms: float | None
    avg_total_tokens: float | None
    total_tokens: int
    estimated_cost: float
    tier0_count: int
    tier0_share: float
    with_tool_calls: int


class LLMLogDetailResponse(LLMLogResponse):
    raw_request: dict | None
    raw_response: dict | None
    normalized_request: dict | None
    normalized_response: dict | None
    request_size_bytes: int | None
    response_size_bytes: int | None
    context_messages_count: int | None
    context_memory_count: int | None
    context_kb_count: int | None
    context_tools_count: int | None
    tokens_system: int | None = None
    tokens_tools: int | None = None
    tokens_memory: int | None = None
    tokens_kb: int | None = None
    tokens_history: int | None = None
    tokens_user: int | None = None
    debug: dict | None = None
