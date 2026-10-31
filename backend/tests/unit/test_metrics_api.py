"""API метрик: /api/metrics/turns и /summary."""

from fastapi.testclient import TestClient

from app.core.agent.metrics import MetricsStore, TurnMetrics
from app.main import app


def test_turns_and_summary_endpoints() -> None:
    metrics = TurnMetrics(session_id="s", turn_id="t", question="q", route="simple_count")
    metrics.record_llm(10, 2, 25)
    metrics.record_stage("planning", 0.5)
    MetricsStore().record(metrics)

    client = TestClient(app)
    response = client.get("/api/metrics/turns", params={"session_id": "s"})
    assert response.status_code == 200
    [turn] = response.json()
    assert turn["session_id"] == "s"
    assert turn["route"] == "simple_count"
    assert turn["llm_calls"] == 1
    assert turn["stages"]["planning"] == 0.5

    summary = client.get("/api/metrics/summary", params={"days": 7})
    assert summary.status_code == 200
    assert summary.json()["turns"] >= 1
    assert summary.json()["llm_calls"] >= 1
