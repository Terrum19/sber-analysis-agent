"""Схемы API метрик ходов (P1)."""

from pydantic import BaseModel, Field


class TurnMetricsOut(BaseModel):
    id: int
    session_id: str
    turn_id: str
    route: str | None = None
    model: str | None = None
    stages: dict[str, float] = Field(default_factory=dict)
    llm_calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_ms: int = 0
    budget_exceeded: str | None = None
    created_at: float


class MetricsSummaryOut(BaseModel):
    days: int
    turns: int
    p50_ms: int | None = None
    p95_ms: int | None = None
    llm_calls: int = 0
    prompt_tokens: int = 0
    budget_exceeded: int = 0
