"""LLMClient: повторяется только невалидный ответ модели, а не 401/404 и не сеть."""

import json

import httpx
import instructor
import openai
import pytest
from openai import AsyncOpenAI
from pydantic import BaseModel

from app.config import settings
from app.core.llm import budget
from app.core.llm.client import LLMClient


class Answer(BaseModel):
    value: int


def tool_call_response(arguments: dict) -> dict:
    return {
        "id": "c1",
        "object": "chat.completion",
        "created": 0,
        "model": "m",
        "choices": [
            {
                "index": 0,
                "finish_reason": "tool_calls",
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "t1",
                            "type": "function",
                            "function": {"name": "Answer", "arguments": json.dumps(arguments)},
                        }
                    ],
                },
            }
        ],
    }


def client_with(responses: list[httpx.Response]) -> tuple[LLMClient, list[httpx.Request]]:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return responses[min(len(requests), len(responses)) - 1]

    llm = object.__new__(LLMClient)
    raw = AsyncOpenAI(
        base_url="http://llm.test/v1",
        api_key="k",
        max_retries=0,
        http_client=httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            event_hooks={"response": [llm._check_context]},
        ),
    )
    llm._client, llm.model, llm._instructor = raw, "m", instructor.from_openai(raw)
    llm.last_prompt_tokens = None
    return llm, requests


async def test_auth_error_is_not_retried() -> None:
    llm, requests = client_with([httpx.Response(401, json={"error": {"message": "no key"}})])
    with pytest.raises(openai.AuthenticationError):
        await llm.complete_structured("s", "u", Answer)
    assert len(requests) == 1


async def test_invalid_answer_is_asked_again() -> None:
    llm, requests = client_with(
        [
            httpx.Response(200, json=tool_call_response({"value": "не число"})),
            httpx.Response(200, json=tool_call_response({"value": 42})),
        ]
    )
    assert (await llm.complete_structured("s", "u", Answer)).value == 42
    assert len(requests) == 2


# --- Длина контекста (I-04) ---


@pytest.fixture
def server(monkeypatch):
    """Мок сервера LLM: ответ чата с заданным usage и его сведения о контексте."""
    monkeypatch.setattr(settings, "llm_num_ctx", 16384)
    monkeypatch.setattr(budget, "_server_ctx", {})
    state = {"prompt_tokens": 100, "models": {"data": [{"id": "m"}]}, "ps": None, "paths": []}

    def handler(request: httpx.Request) -> httpx.Response:
        state["paths"].append(request.url.path)
        if request.url.path == "/v1/models":
            return httpx.Response(200, json=state["models"])
        if request.url.path == "/api/ps":
            return httpx.Response(200 if state["ps"] else 404, json=state["ps"] or {})
        answer = {
            "id": "c1",
            "object": "chat.completion",
            "created": 0,
            "model": "m",
            "choices": [
                {
                    "index": 0,
                    "finish_reason": "stop",
                    "message": {"role": "assistant", "content": "ок"},
                }
            ],
            "usage": {
                "prompt_tokens": state["prompt_tokens"],
                "completion_tokens": 1,
                "total_tokens": state["prompt_tokens"] + 1,
            },
        }
        return httpx.Response(200, json=answer)

    llm = object.__new__(LLMClient)
    raw = AsyncOpenAI(
        base_url="http://llm.test/v1",
        api_key="k",
        max_retries=0,
        http_client=httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            event_hooks={"response": [llm._check_context]},
        ),
    )
    llm._client, llm.model, llm.last_prompt_tokens = raw, "m", None
    return llm, state


async def test_ollama_truncation_is_reported(server, caplog) -> None:
    llm, state = server
    state["prompt_tokens"] = 4096
    state["ps"] = {"models": [{"name": "m", "model": "m", "context_length": 4096}]}
    collected = budget.track_truncations()

    assert await llm.complete("с" * 12_000, "вопрос") == "ок"

    assert budget.server_context("http://llm.test/v1", "m") == 4096
    assert "меньше LLM_NUM_CTX" in caplog.text
    assert "Промпт обрезан сервером LLM" in caplog.text
    assert collected[0]["prompt_tokens"] == 4096
    assert llm.last_prompt_tokens == 4096


async def test_far_fewer_tokens_than_estimate_is_reported(server, caplog) -> None:
    llm, state = server
    state["prompt_tokens"] = 500
    await llm.complete("с" * 12_000, "вопрос")
    assert "меньше 70% оценки" in caplog.text


async def test_full_prompt_is_not_reported(server, caplog) -> None:
    llm, state = server
    state["prompt_tokens"] = 4700
    state["models"] = {"data": [{"id": "m", "max_model_len": 16384}]}  # vLLM
    await llm.complete("с" * 12_000, "вопрос")
    await llm.complete("с" * 12_000, "вопрос")

    assert "Промпт обрезан" not in caplog.text
    assert budget.server_context("http://llm.test/v1", "m") == 16384
    # Контекст спрашиваем один раз, /api/ps не нужен: vLLM ответил в /v1/models.
    assert state["paths"].count("/v1/models") == 1
    assert "/api/ps" not in state["paths"]


async def test_broken_server_info_does_not_fail_the_call(server, caplog) -> None:
    llm, state = server
    state["ps"] = {"models": [{"name": "m", "context_length": "много"}]}
    assert await llm.complete("с", "вопрос") == "ок"
    assert "Проверка контекста LLM не удалась" in caplog.text
