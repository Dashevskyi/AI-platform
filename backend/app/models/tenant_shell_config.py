import uuid
from datetime import datetime, timezone

from sqlalchemy import String, Boolean, DateTime, Text, Float, Integer, ForeignKey
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class TenantShellConfig(Base):
    __tablename__ = "tenant_shell_configs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, unique=True, index=True)
    provider_type: Mapped[str] = mapped_column(String(50), nullable=False, default="ollama")
    provider_base_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    provider_api_key_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    model_name: Mapped[str] = mapped_column(String(200), nullable=False, default="qwen2.5:32b")
    system_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Domain ontology: structure of entities, terminology, tool ↔ argument mapping.
    # Separate from system_prompt so admins keep "who you are" and "what data exists" apart.
    ontology_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    rules_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    temperature: Mapped[float] = mapped_column(Float, default=0.7)
    max_context_messages: Mapped[int] = mapped_column(Integer, default=20)
    # Token budget for the prompt history block. Layered by recency:
    # last pairs verbatim (native roles) → older pairs as one-line resumes,
    # newest-first while the budget lasts → beyond that, the rolling chat
    # summary. ~3 chars/token estimate for Cyrillic.
    history_budget_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=3000)
    max_tokens: Mapped[int] = mapped_column(Integer, default=4096)
    summary_model_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    context_mode: Mapped[str] = mapped_column(String(50), default="summary_plus_recent")
    memory_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    knowledge_base_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    embedding_model_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    vision_model_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    kb_max_chunks: Mapped[int] = mapped_column(Integer, default=10)
    # When True (default): top-K KB chunks are automatically injected into the
    # system prompt before the first LLM call (eager / pre-loaded).
    # When False: no KB in system prompt; the model calls `search_kb(query=...)`
    # on demand. Saves ~1800 tokens per request — prompt stays under the
    # auto-router light-model threshold for most queries. Trade-off: queries
    # that genuinely need KB info get one extra LLM round (~1s).
    kb_inject_auto: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    tools_policy: Mapped[str] = mapped_column(String(50), default="auto")
    # Qwen3 thinking mode: "on" (always reason), "off" (never), "auto" (heuristic — short/simple → off)
    enable_thinking: Mapped[str] = mapped_column(String(10), default="on")
    # If True, the built-in `recall_chat` tool can search across other chats of the
    # same tenant (default — only the current chat).
    recall_cross_chat_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    # Default language for ALL LLM responses (BCP-47-ish short tag: "ru", "uk", "en", "pl", ...).
    # Used to build a language-pin system message injected into every service call
    # (chat, summary, resume) so the model doesn't switch languages on its own.
    response_language: Mapped[str] = mapped_column(String(8), nullable=False, default="ru")
    # IANA timezone (e.g. "Europe/Kyiv", "Asia/Tokyo", "UTC"). Used to render the
    # "current date" system-prompt block in the tenant's local time. If NULL —
    # pipeline falls back to the server's local timezone.
    timezone: Mapped[str | None] = mapped_column(String(60), nullable=True)
    # Cosine similarity floor for semantic tool selection. Tools below this
    # threshold are excluded from the LLM payload — prevents noisy "kinda
    # matches" from crowding the prompt and stealing model attention.
    tool_semantic_floor: Mapped[float] = mapped_column(Float, nullable=False, default=0.5)
    # Temperature override for LLM rounds that have tools in their payload.
    # Lower temp = more deterministic tool choice; helps weak models that
    # otherwise pick wrong tool or invent arguments. Ordinary chat (no tools
    # in payload) keeps the higher `temperature` since creativity is still wanted.
    tool_routing_temperature: Mapped[float] = mapped_column(Float, nullable=False, default=0.3)
    # Lazy tool catalog: only the top-N tools by semantic score go to the model
    # with their full schema (description + parameters). The rest are listed
    # compactly (name + 1-line). Model can fetch their schema via
    # `describe_tool(name)` builtin, or just call them by name — the pipeline
    # adds the missing schema to the payload on the next round automatically.
    # Set to a large value (e.g. 100) to disable the feature.
    lazy_tool_catalog_topk: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    # Hard cap on tool-routing loop iterations. Stops the "model keeps asking
    # for one more tool" runaway. Default 6 is enough for typical PON workflow
    # (search → tree → path → ddm); raise only for tenants doing multi-stage
    # data pipelines that genuinely need >6 steps.
    max_tool_rounds: Mapped[int] = mapped_column(Integer, nullable=False, default=6)
    # Auto tool-limit: when True, replace the flat max_tool_rounds cap with
    # intent-aware guards — stop only when the model is clearly lost (repeated
    # failures or hammering one tool), and grant a larger budget once a plan
    # exists. Lets legitimate multi-step work run while still killing runaways.
    tool_limit_auto: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    tool_limit_max_failures: Mapped[int] = mapped_column(Integer, nullable=False, default=4)
    tool_limit_max_per_tool: Mapped[int] = mapped_column(Integer, nullable=False, default=4)
    tool_limit_plan_rounds: Mapped[int] = mapped_column(Integer, nullable=False, default=20)
    # Per-tenant switch for accumulating the debug JSON on every LLM call.
    # On while we're collecting trace data for analysis; off for "we know
    # this tenant works, stop bloating llm_request_logs" mode.
    debug_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # Tier 0 routing: deterministic shortcut for trivial queries that match
    # exactly one tool with high confidence. Skips LLM entirely — calls
    # tool directly + renders its output through a template. See
    # services/llm/tier0_router.py for activation criteria.
    tier0_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    tier0_min_tool_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.80)
    tier0_max_score_gap: Mapped[float] = mapped_column(Float, nullable=False, default=0.15)
    # PII routing safeguard: when ON, any user message that contains an
    # extractable PII entity (phone / MAC / IP) forces the AutoRouter to
    # stay on the LOCAL model for the rest of this chat — no escalation to
    # cloud (DeepSeek/Claude) regardless of complexity or context size.
    # Trades reasoning quality for data residency. Opt-in per tenant.
    pii_routing_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # STT domain vocabulary — improves recognition of tenant-specific terminology
    # (ISP jargon, city streets, product names, etc.).
    # stt_initial_prompt: primes the Whisper decoder; model sees this "transcript"
    #   before the audio — boosts probability of known words in the vocabulary.
    # stt_hotwords: space-separated list boosted during beam search (faster-whisper
    #   hotwords parameter). Separate from initial_prompt for clarity.
    stt_initial_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    stt_hotwords: Mapped[str | None] = mapped_column(Text, nullable=True)
    # STT post-processing: vocabulary source for transcript normalization.
    # After Whisper returns text, we fuzzy-match each word against a tenant
    # vocabulary loaded from an external source and correct mis-transcriptions
    # (e.g. "свАче" → "свиче", "косарова" → "Косарева").
    #
    # stt_vocab_source — JSON descriptor of the source:
    #   {"type": "sql",  "query": "SELECT DISTINCT street FROM subs WHERE ..."}
    #   {"type": "tool", "tool_name": "search_clients", "field": "address_street"}
    #   {"type": "http", "url": "https://...", "jq": ".streets[]"}
    # stt_vocab_source_dsn_enc — encrypted connection string for "sql" type.
    # stt_fuzzy_threshold — minimum similarity score (0-100) to accept a match.
    #   Default 85 catches 1-char edits like "косарова"→"Косарева" (87.5% ratio).
    #   False positives are handled via stt_vocab_source["blacklist"] per-tenant.
    stt_vocab_source: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    stt_vocab_source_dsn_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    stt_fuzzy_threshold: Mapped[float] = mapped_column(Float, nullable=False, default=85.0)
    # Per-tenant TTS configuration.
    # tts_provider: 'system' (use global .env defaults), 'elevenlabs' (tenant's own key),
    #               'fish_speech' (use local Fish Speech, optionally with custom URL).
    # When NULL → treated as 'system'.
    tts_provider: Mapped[str | None] = mapped_column(String(20), nullable=True)
    tts_api_key_enc: Mapped[str | None] = mapped_column(Text, nullable=True)   # encrypted ElevenLabs key
    tts_voice_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    tts_model: Mapped[str | None] = mapped_column(String(200), nullable=True)
    tts_speed: Mapped[float | None] = mapped_column(Float, nullable=True)
    tts_pitch: Mapped[str | None] = mapped_column(String(10), nullable=True)  # x-low|low|medium|high|x-high (Silero SSML)
    # Voice-mode hold phrases («Секунду…») while the LLM thinks.
    voice_hold_enabled: Mapped[bool | None] = mapped_column(Boolean, nullable=True)   # NULL → True
    voice_hold_delay_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)   # NULL → 1600
    voice_hold_phrases: Mapped[str | None] = mapped_column(Text, nullable=True)       # newline-separated; NULL → builtins
    tts_fish_url: Mapped[str | None] = mapped_column(String(500), nullable=True)  # custom Fish Speech base URL
    # Deterministic anti-hallucination guard for sensitive (e.g. payment) links.
    # Generic — patterns/domains live here, NOT in code. Shape:
    #   {"enabled": true,
    #    "sensitive_patterns": ["liqpay.ua", "pay.example.com"],  # substrings; a URL matching any is "sensitive"
    #    "allowlist": ["cabinet.example.com"],                    # always-trusted substrings
    #    "fallback_url": "https://cabinet.example.com",           # replace unverified sensitive URLs with this
    #    "fallback_text": null}                                   # used if no fallback_url
    # A sensitive URL in the model's answer that did NOT appear verbatim in any
    # tool result / KB chunk this turn is treated as fabricated and rewritten.
    link_guard: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    # Editable override of the static system-prompt blocks. NULL → code defaults
    # (STATIC_SYSTEM_BLOCKS). List of {label, content, enabled}. See system_blocks.py.
    system_blocks: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
