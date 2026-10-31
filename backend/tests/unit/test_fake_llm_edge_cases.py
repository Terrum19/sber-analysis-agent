"""Граничные случаи FakeLLM."""

import asyncio

import pytest
from pydantic import BaseModel

from app.core.llm.fake_client import FakeLLM


class UnknownSchema(BaseModel):
    name: str = ""


@pytest.fixture
def fakellm():
    return FakeLLM()


def test_fakellm_unknown_schema_returns_empty_instance(fakellm):
    result = asyncio.run(fakellm.complete_structured("sys", "q", UnknownSchema))
    assert isinstance(result, UnknownSchema)
    assert result.name == ""


def test_fakellm_complete_returns_fixed_string(fakellm):
    result = asyncio.run(fakellm.complete("sys", "q"))
    assert result == "Fake LLM response"
