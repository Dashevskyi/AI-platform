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
    history_budget_tokens: int | None = None
    max_tokens: int | None = None
    summary_model_name: str | None = None
    context_mode: str | None = None
    memory_enabled: bool | None = None
    knowledge_base_enabled: bool | None = None
    embedding_model_name: str | None = None
    vision_model_name: str | None = None
    kb_max_chunks: int | None = None
    kb_inject_auto: bool | None = None
    tools_policy: str | None = None
    enable_thinking: str | None = None
    response_language: str | None = None
    debug_enabled: bool | None = None
    timezone: str | None = None
    tool_semantic_floor: float | None = None
    tool_routing_temperature: float | None = None
    lazy_tool_catalog_topk: int | None = None
    max_tool_rounds: int | None = None
    tier0_enabled: bool | None = None
    tier0_min_tool_score: float | None = None
    tier0_max_score_gap: float | None = None
    pii_routing_enabled: bool | None = None
    stt_initial_prompt: str | None = None
    stt_hotwords: str | None = None
    stt_vocab_source: dict | None = None
    stt_vocab_source_dsn: str | None = None   # plaintext DSN — encrypted on save
    stt_fuzzy_threshold: float | None = None
    # TTS config — 'system' | 'elevenlabs' | 'fish_speech'
    tts_provider: str | None = None
    tts_api_key: str | None = None     # plaintext ElevenLabs API key — encrypted on save
    tts_voice_id: str | None = None
    tts_model: str | None = None
    tts_speed: float | None = None
    tts_pitch: str | None = None
    voice_hold_enabled: bool | None = None
    voice_hold_delay_ms: int | None = None
    voice_hold_phrases: str | None = None
    tts_fish_url: str | None = None    # custom Fish Speech base URL override


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
    history_budget_tokens: int = 3000
    max_tokens: int
    summary_model_name: str | None
    context_mode: str
    memory_enabled: bool
    knowledge_base_enabled: bool
    embedding_model_name: str | None
    vision_model_name: str | None = None
    kb_max_chunks: int
    kb_inject_auto: bool = True
    tools_policy: str
    enable_thinking: str
    response_language: str = "ru"
    debug_enabled: bool = True
    timezone: str | None = None
    tool_semantic_floor: float = 0.5
    tool_routing_temperature: float = 0.3
    lazy_tool_catalog_topk: int = 3
    max_tool_rounds: int = 6
    tier0_enabled: bool = False
    tier0_min_tool_score: float = 0.80
    tier0_max_score_gap: float = 0.15
    pii_routing_enabled: bool = False
    stt_initial_prompt: str | None = None
    stt_hotwords: str | None = None
    stt_vocab_source: dict | None = None
    stt_vocab_source_dsn_masked: str | None = None   # masked for display
    stt_fuzzy_threshold: float = 85.0
    # TTS
    tts_provider: str = 'system'
    tts_api_key_masked: str | None = None
    tts_voice_id: str | None = None
    tts_model: str | None = None
    tts_speed: float | None = None
    tts_pitch: str | None = None
    voice_hold_enabled: bool | None = None
    voice_hold_delay_ms: int | None = None
    voice_hold_phrases: str | None = None
    tts_fish_url: str | None = None

    model_config = {"from_attributes": True}


class VocabRebuildResponse(BaseModel):
    terms_count: int
    sample: list[str]
    cached_at: float


class TestConnectionRequest(BaseModel):
    provider_type: str
    provider_base_url: str | None = None
    provider_api_key: str | None = None
    model_name: str | None = None


class TestConnectionResponse(BaseModel):
    success: bool
    message: str
    models: list[str] | None = None
