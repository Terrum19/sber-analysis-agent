"""Тест переключения LLMClient через env var без monkeypatch."""

import os

import pytest

from app.core.llm.client import LLMClient
from app.core.llm.fake_client import FakeLLM
from app.models.research_step import ResearchPlan


@pytest.fixture(autouse=True)
def _set_fake_llm_true():
    os.environ["USE_FAKE_LLM"] = "true"
    yield
    os.environ.pop("USE_FAKE_LLM", None)


def test_llm_client_returns_fakellm_via_env():
    client = LLMClient()
    assert isinstance(client, FakeLLM)
    assert client.model == "fake-llm"


def test_llm_client_fake_has_complete_methods():
    import asyncio

    client = LLMClient()
    result = asyncio.run(client.complete("sys", "user"))
    assert isinstance(result, str)
    plan_result = asyncio.run(client.complete_structured("sys", "user", ResearchPlan))
    assert isinstance(plan_result, ResearchPlan)
