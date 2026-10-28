"""Метрики ходов агента: /api/metrics/turns и /summary (P1)."""

from fastapi import APIRouter, Query

from app.api.schemas.metrics import MetricsSummaryOut, TurnMetricsOut
from app.core.agent.metrics import MetricsStore

router = APIRouter()


@router.get("/turns", response_model=list[TurnMetricsOut])
def turns(
    limit: int = Query(default=100, ge=1, le=1000),
    session_id: str | None = Query(default=None),
) -> list[TurnMetricsOut]:
    """Последние ходы с route, llm_calls, токенами и длительностью."""
    return [TurnMetricsOut(**row) for row in MetricsStore().list_turns(limit, session_id)]


@router.get("/summary", response_model=MetricsSummaryOut)
def summary(days: int = Query(default=7, ge=1, le=365)) -> MetricsSummaryOut:
    """p50/p95 до done и число превышений бюджета за период."""
    return MetricsSummaryOut(**MetricsStore().summary(days))
