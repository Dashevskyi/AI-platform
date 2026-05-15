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


class TenantStatsResponse(BaseModel):
    summary: StatsSummary
    daily: list[DailyModelStats]
