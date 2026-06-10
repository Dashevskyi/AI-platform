from datetime import date
from pydantic import BaseModel


class DailyModelStats(BaseModel):
    date: date
    model_name: str
    total_tokens: int
    prompt_tokens: int
    completion_tokens: int
    estimated_cost: float
    request_count: int


class StatsSummary(BaseModel):
    total_tokens: int
    prompt_tokens: int
    completion_tokens: int
    estimated_cost: float
    request_count: int


class TierStats(BaseModel):
    served_by: str          # 'tier0_template' | 'llm'
    request_count: int
    estimated_cost: float


class BreakdownRow(BaseModel):
    """A by-model or by-API-key slice of usage."""
    key: str
    label: str | None = None  # human label (key name); for models == key
    request_count: int
    total_tokens: int
    estimated_cost: float


class TenantStatsResponse(BaseModel):
    summary: StatsSummary
    daily: list[DailyModelStats]
    # Deterministic (Tier 0, $0) vs LLM split, and the Tier 0 share of traffic.
    tiers: list[TierStats] = []
    tier0_share: float = 0.0
    by_model: list[BreakdownRow] = []
    by_key: list[BreakdownRow] = []
