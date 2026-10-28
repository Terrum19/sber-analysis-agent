"""LLMClient: бюджет, max_tokens, json_object и метрики."""

import json

import httpx
import pytest

from app.config import settings
from app.core.agent.budget import BudgetExceeded, TurnBudget, reset_budget, set_budget
from app.core.agent.metrics import TurnMetrics, reset_metrics, set_metrics
from app.core.llm.client import LLMClient


def response(content: str = "ok") -> dict:
    return {
        "id": "c1",
        "object": "chat.completion",
        "created": 0,
        "model": "m",
        "choices": [
            {
                "index": 0,
                "finish_reason": "stop",
                "message": {"role": "assistant", "content": content},
            }
        ],
        "usage": {"prompt_tokens": 11, "completion_tokens": 2, "total_tokens": 13},
    }


def make_client(requests: list[dict]) -> LLMClient:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/chat/completions"):
            requests.append(json.loads(request.content))
            return httpx.Response(200, json=response())
        return httpx.Response(404, json={})

    return LLMClient(
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        base_url="http://llm.test/v1",
        model="m",
        timeout_s=5,
    )


async def test_max_tokens_and_metrics_are_applied(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "llm_max_tokens", 123)
    llm = make_client(requests := [])
    metrics = TurnMetrics(session_id="s", turn_id="t", question="q")
    token = set_metrics(metrics)
    try:
        assert await llm.complete("s", "u") == "ok"
    finally:
        reset_metrics(token)
    assert requests[0]["max_tokens"] == 123
    assert metrics.llm_calls == 1
    assert metrics.prompt_tokens == 11
    assert metrics.completion_tokens == 2
    assert metrics.last_latency_ms() is not None


async def test_llm_call_limit_is_enforced_before_network(monkeypatch: pytest.MonkeyPatch) -> None:
    llm = make_client(requests := [])
    budget = TurnBudget(total_s=10, max_llm_calls=1, stage_limits={"llm": 10})
    token = set_budget(budget)
    try:
        await llm.complete("s", "u")
        with pytest.raises(BudgetExceeded, match="лимит вызовов"):
            await llm.complete("s", "u")
    finally:
        reset_budget(token)
    assert len(requests) == 1


async def test_json_object_mode_requests_json(monkeypatch: pytest.MonkeyPatch) -> None:
    from pydantic import BaseModel

    class Answer(BaseModel):
        value: int

    monkeypatch.setattr(settings, "llm_structured_mode", "json_object")
    requests: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        return httpx.Response(200, json=response('{"value": 7}'))

    llm = LLMClient(
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        base_url="http://llm.test/v1",
        model="m",
    )
    assert (await llm.complete_structured("s", "u", Answer)).value == 7
    assert requests[0]["response_format"] == {"type": "json_object"}
    assert requests[0]["max_tokens"] == settings.llm_max_tokens
