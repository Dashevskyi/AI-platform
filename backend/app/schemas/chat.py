from datetime import datetime
from pydantic import BaseModel


class ChatCreate(BaseModel):
    title: str | None = None
    description: str | None = None
    assistant_id: str | None = None


class ChatUpdate(BaseModel):
    title: str | None = None
    description: str | None = None
    status: str | None = None
    # Pass an empty string or null to clear the flag; non-empty to set it.
    flagged_issue: str | None = None


class ChatResponse(BaseModel):
    id: str
    tenant_id: str
    api_key_id: str | None = None
    assistant_id: str | None = None
    assistant_name: str | None = None
    title: str | None
    description: str | None
    status: str
    created_by: str | None
    created_at: datetime
    updated_at: datetime
    flagged_issue: str | None = None
    flagged_at: datetime | None = None
    message_count: int | None = None

    model_config = {"from_attributes": True}


class ActorGeo(BaseModel):
    lat: float
    lng: float
    accuracy_m: float | None = None


class Actor(BaseModel):
    """Who the request is on behalf of — supplied by the channel (CRM), NOT the
    model. The platform renders this into a trusted "## Текущий пользователь"
    system block and exposes the fields to tools, instead of the channel
    stuffing identity into the prompt (which mixes trusted identity with
    untrusted user text → prompt-injection risk).

    All fields optional: a channel may pass just an `external_id` (the platform
    enriches it later via a resolver) or pre-filled display fields. `geo` is
    ephemeral (the tablet's live position) and only the channel knows it, so it
    must travel in the request — but as structured data, not prose.
    """
    external_id: str | None = None        # stable id in the channel/CRM (CSV → IN)
    role: str | None = None               # installer | subscriber | dispatcher | ...
    phone: str | None = None              # verified phone (for forced sms_phone match)
    display_name: str | None = None
    # Free key→value the platform renders verbatim (бригада, договор, …).
    attributes: dict[str, str] | None = None
    geo: ActorGeo | None = None


class MessageSend(BaseModel):
    content: str
    idempotency_key: str | None = None
    # When True the request originates from voice input (STT). The pipeline
    # forces enable_thinking=False to avoid the +5 s TTFT penalty from the
    # reasoning warmup, which is unacceptable in real-time voice UX.
    voice_mode: bool = False
    # Verified identity/context of the asker (technician, subscriber, …),
    # supplied by the channel. Rendered into a trusted system block.
    actor: Actor | None = None


class MessageResponse(BaseModel):
    id: str
    tenant_id: str
    chat_id: str
    role: str
    content: str
    metadata_json: dict | None = None
    prompt_tokens: int | None
    completion_tokens: int | None
    total_tokens: int | None
    latency_ms: float | None
    time_to_first_token_ms: float | None = None
    provider_type: str | None = None
    model_name: str | None = None
    correlation_id: str | None = None
    tool_calls_count: int | None = None
    finish_reason: str | None = None
    status: str
    created_at: datetime

    model_config = {"from_attributes": True}


class PublicMessageResponse(BaseModel):
    """End-user-facing message shape for the tenant API.

    Strips internal metadata: no events trail, no reasoning, no tool/provider
    details, no token counts, no model name. End clients (CRMs, embedded
    chat widgets) should not see these.
    """
    id: str
    chat_id: str
    role: str
    content: str
    status: str
    created_at: datetime

    model_config = {"from_attributes": True}
