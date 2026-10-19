"""Интеграционный тест на USE_FAKE_LLM=true без monkeypatch.

Проверяет, что при установке переменной окружения USE_FAKE_LLM=true:
- LLMClient() возвращает FakeLLM (через __new__)
- __init__ не вызывается на возвращённом FakeLLM
- complete_structured возвращает валидные ResearchPlan и Insight
"""

import asyncio
import os

import pytest

from app.core.llm.client import LLMClient
from app.core.llm.fake_client import FakeLLM
from app.models.insight import Insight
from app.models.research_step import ResearchPlan


@pytest.fixture(autouse=True)
def _set_fake_llm_env():
    """Устанавливает USE_FAKE_LLM=true для всего модуля."""
    os.environ["USE_FAKE_LLM"] = "true"
    yield
    os.environ.pop("USE_FAKE_LLM", None)


def test_llm_client_returns_fakellm_without_monkeypatch() -> None:
    """LLMClient() при USE_FAKE_LLM=true возвращает FakeLLM."""
    client = LLMClient()
    assert isinstance(client, FakeLLM)
    assert client.model == "fake-llm"


def test_fakellm_complete_structured_returns_research_plan() -> None:
    """FakeLLM.complete_structured(ResearchPlan) возвращает валидный план."""
    client = LLMClient()
    result = asyncio.run(client.complete_structured("sys", "q", ResearchPlan))
    assert isinstance(result, ResearchPlan)
    assert len(result.steps) == 1
    assert result.steps[0].tool == "aggregate"
    assert result.final_viz == "bar"


def test_fakellm_complete_structured_returns_insight() -> None:
    """FakeLLM.complete_structured(Insight) возвращает валидный инсайт."""
    client = LLMClient()
    result = asyncio.run(client.complete_structured("sys", "q", Insight))
    assert isinstance(result, Insight)
    assert result.headline == "Исследование завершено"
    assert len(result.details) > 0


def test_fakellm_complete_returns_string() -> None:
    """FakeLLM.complete возвращает строку."""
    client = LLMClient()
    result = asyncio.run(client.complete("sys", "q"))
    assert isinstance(result, str)
    assert result == "Fake LLM response"
