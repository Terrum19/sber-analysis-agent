"""LLMClient: повторяется только невалидный ответ модели, а не 401/404 и не сеть."""

import asyncio
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


def text_response(
    content: str, finish_reason: str = "stop", reasoning_tokens: int | None = None
) -> dict:
    body = {
        "id": "c1",
        "object": "chat.completion",
        "created": 0,
        "model": "m",
        "choices": [
            {
                "index": 0,
                "finish_reason": finish_reason,
                "message": {"role": "assistant", "content": content},
            }
        ],
    }
    if reasoning_tokens is not None:
        body["usage"] = {
            "prompt_tokens": 10,
            "completion_tokens": reasoning_tokens,
            "total_tokens": 10 + reasoning_tokens,
            "completion_tokens_details": {"reasoning_tokens": reasoning_tokens},
        }
    return body


def client_with(responses: list[httpx.Response]) -> tuple[LLMClient, list[httpx.Request]]:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        # Служебные запросы контекста (/models, /api/ps) не вызовы LLM:
        # в счётчик и в заготовленные ответы они не идут.
        if request.url.path != "/v1/chat/completions":
            return httpx.Response(404, json={"error": {"message": "not found"}})
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


async def test_invalid_answer_is_asked_again(monkeypatch: pytest.MonkeyPatch) -> None:
    # Переспрос instructor есть только в режиме tools.
    monkeypatch.setattr(settings, "llm_structured_mode", "tools")
    llm, requests = client_with(
        [
            httpx.Response(200, json=tool_call_response({"value": "не число"})),
            httpx.Response(200, json=tool_call_response({"value": 42})),
        ]
    )
    assert (await llm.complete_structured("s", "u", Answer)).value == 42
    assert len(requests) == 2


def chat_response(content: str) -> dict:
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
    }


@pytest.mark.parametrize(
    ("effort", "reasoning"),
    [(None, None), ("none", {"enabled": False}), ("low", {"effort": "low"})],
)
async def test_reasoning_effort_goes_to_request(monkeypatch, effort, reasoning) -> None:
    # Без размышлений провайдеры OpenRouter не ломают json_schema-план (docs/eval.md).
    monkeypatch.setattr(settings, "llm_reasoning_effort", effort)
    llm, requests = client_with([httpx.Response(200, json=chat_response("ок"))])
    assert await llm.complete("s", "u") == "ок"
    assert json.loads(requests[0].content).get("reasoning") == reasoning


@pytest.mark.parametrize(
    ("provider", "routing"),
    [(None, None), ("deepinfra", {"only": ["deepinfra"]}), ("a, b", {"only": ["a", "b"]})],
)
async def test_provider_goes_to_request(monkeypatch, provider, routing) -> None:
    # С размышлениями json_schema-план держат не все провайдеры (docs/eval.md).
    monkeypatch.setattr(settings, "llm_provider", provider)
    llm, requests = client_with([httpx.Response(200, json=chat_response("ок"))])
    assert await llm.complete("s", "u") == "ок"
    assert json.loads(requests[0].content).get("provider") == routing


async def test_length_cut_answer_is_retried_with_more_tokens(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Reasoning-модель потратила весь max_tokens на рассуждение: content пуст,
    # finish_reason=length. Повтор с увеличенным лимитом возвращает ответ.
    monkeypatch.setattr(settings, "llm_max_tokens", 16)
    monkeypatch.setattr(settings, "llm_length_retry_tokens", 256)
    llm, requests = client_with(
        [
            httpx.Response(200, json=text_response("", "length")),
            httpx.Response(200, json=text_response("ок")),
        ]
    )
    assert await llm.complete("s", "u") == "ок"
    assert len(requests) == 2
    assert json.loads(requests[0].content)["max_tokens"] == 16
    assert json.loads(requests[1].content)["max_tokens"] == 256


async def test_length_retry_happens_once(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "llm_max_tokens", 16)
    monkeypatch.setattr(settings, "llm_length_retry_tokens", 256)
    llm, requests = client_with(
        [
            httpx.Response(200, json=text_response("", "length")),
            httpx.Response(200, json=text_response("", "length")),
        ]
    )
    assert await llm.complete("s", "u") == ""
    assert len(requests) == 2


async def test_reasoning_cut_answer_is_retried_without_reasoning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # И увеличенный лимит целиком ушёл в рассуждения: пустой ответ хуже
    # ответа без скрытых рассуждений, поэтому последняя попытка — без них.
    monkeypatch.setattr(settings, "llm_max_tokens", 16)
    monkeypatch.setattr(settings, "llm_length_retry_tokens", 256)
    monkeypatch.setattr(settings, "llm_num_ctx", 16384)
    llm, requests = client_with(
        [
            httpx.Response(200, json=text_response("", "length", reasoning_tokens=16)),
            httpx.Response(200, json=text_response("", "length", reasoning_tokens=256)),
            httpx.Response(200, json=text_response('{"value": 1}')),
        ]
    )
    assert await llm.complete("s", "u") == '{"value": 1}'
    assert len(requests) == 3
    retry = json.loads(requests[2].content)
    assert retry["reasoning"] == {"enabled": False}
    assert retry["max_tokens"] == 256


async def test_reasoning_retry_not_made_without_reasoning_tokens(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Провайдер не сообщил reasoning_tokens (Ollama/vLLM) — лишнего вызова нет.
    monkeypatch.setattr(settings, "llm_max_tokens", 16)
    monkeypatch.setattr(settings, "llm_length_retry_tokens", 256)
    llm, requests = client_with(
        [
            httpx.Response(200, json=text_response("", "length")),
            httpx.Response(200, json=text_response("", "length")),
        ]
    )
    assert await llm.complete("s", "u") == ""
    assert len(requests) == 2


# --- Длина контекста (I-04) ---


@pytest.fixture
def server(monkeypatch):
    """Мок сервера LLM: ответ чата с заданным usage и его сведения о контексте."""
    monkeypatch.setattr(settings, "llm_num_ctx", 16384)
    monkeypatch.setattr(budget, "_server_ctx", {})
    monkeypatch.setattr(budget, "_server_ctx_asked", {})
    state = {
        "prompt_tokens": 100,
        "models": {"data": [{"id": "m"}]},
        "ps": None,
        "paths": [],
        "down": False,
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if state["down"]:
            raise httpx.ConnectError("LLM не поднялась", request=request)
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


async def test_tools_schema_counts_toward_prompt_length(server, caplog) -> None:
    llm, state = server
    # Текст — 3000 символов (оценка 1000 токенов), схема тулов — ещё ~6000.
    # 1500 токенов больше 70 % оценки по тексту, но меньше 70 % оценки вместе со схемой.
    state["prompt_tokens"] = 1500
    tool = {
        "type": "function",
        "function": {
            "name": "plan",
            "description": "д" * 6000,
            "parameters": {"type": "object", "properties": {}},
        },
    }
    await llm._client.chat.completions.create(
        model="m", messages=[{"role": "user", "content": "с" * 3000}], tools=[tool]
    )
    assert "меньше 70% оценки" in caplog.text


async def test_server_context_is_asked_again_after_failure(server, monkeypatch) -> None:
    llm, state = server
    await llm.complete("с", "вопрос")  # /api/ps не ответил — контекст неизвестен
    assert budget.server_context("http://llm.test/v1", "m") is None

    state["ps"] = {"models": [{"name": "m", "model": "m", "context_length": 4096}]}
    await llm.complete("с", "вопрос")
    assert state["paths"].count("/api/ps") == 1  # сразу не переспрашиваем

    monkeypatch.setattr(budget, "SERVER_CTX_RETRY_S", 0)
    await llm.complete("с", "вопрос")
    assert budget.server_context("http://llm.test/v1", "m") == 4096


async def test_warm_up_learns_server_context(server) -> None:
    llm, state = server
    state["models"] = {"data": [{"id": "m", "max_model_len": 8192}]}
    await llm.warm_up()
    assert budget.server_context("http://llm.test/v1", "m") == 8192

    await llm.warm_up()  # контекст уже известен — запроса нет
    assert state["paths"].count("/v1/chat/completions") == 1


async def test_warm_up_survives_unavailable_llm(server, caplog) -> None:
    llm, state = server
    state["down"] = True
    await llm.warm_up()
    assert "LLM недоступна при старте" in caplog.text
    assert budget.server_context("http://llm.test/v1", "m") is None


async def test_call_line_has_model_schema_time_and_tokens(server, caplog) -> None:
    import logging

    llm, state = server
    state["prompt_tokens"] = 321
    caplog.set_level(logging.INFO, logger="app.core.llm.client")
    response_format = {"type": "json_schema", "json_schema": {"name": "Answer", "schema": {}}}
    await llm.complete("системный промпт", "вопрос", response_format=response_format)
    lines = [entry.getMessage() for entry in caplog.records if entry.name == "app.core.llm.client"]
    assert len(lines) == 1
    assert lines[0].startswith("LLM m: схема=Answer за ")
    assert lines[0].endswith(", prompt_tokens=321, ответ 2 символов")
    assert "системный промпт" not in caplog.text


async def test_prompt_and_answer_only_at_debug(server, caplog) -> None:
    import logging

    llm, _ = server
    caplog.set_level(logging.DEBUG, logger="app.core.llm.client")
    await llm.complete("системный промпт", "вопрос")
    assert "системный промпт" in caplog.text
    assert "'ок'" in caplog.text
    assert "схема=-" in caplog.text


@pytest.mark.parametrize(
    ("failure", "kind"),
    [
        (
            httpx.ConnectError("отказ", request=httpx.Request("POST", "http://llm.test")),
            "соединение",
        ),
        (httpx.ReadTimeout("долго", request=httpx.Request("POST", "http://llm.test")), "таймаут"),
        (httpx.Response(503, json={"error": {"message": "перегружен"}}), "HTTP 503"),
    ],
)
async def test_failed_call_names_kind_address_and_wait(failure, kind, caplog) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if isinstance(failure, Exception):
            raise failure
        return failure

    llm = object.__new__(LLMClient)
    raw = AsyncOpenAI(
        base_url="http://user:secret@llm.test/v1",
        api_key="k",
        max_retries=0,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    llm._client, llm.model = raw, "m"
    with pytest.raises(openai.APIError):
        await llm.complete("s", "u")
    errors = [entry.getMessage() for entry in caplog.records if entry.levelname == "ERROR"]
    assert len(errors) == 1
    assert errors[0].startswith(f"LLM m: сбой ({kind}) http://llm.test/v1/, ждали ")
    assert "secret" not in caplog.text


async def test_failed_tools_call_is_logged(monkeypatch, caplog) -> None:
    monkeypatch.setattr(settings, "llm_structured_mode", "tools")
    llm, _ = client_with([httpx.Response(401, json={"error": {"message": "no key"}})])
    with pytest.raises(openai.AuthenticationError):
        await llm.complete_structured("s", "u", Answer)
    assert "LLM m: сбой (HTTP 401)" in caplog.text


def test_unknown_failure_is_named_by_type() -> None:
    from app.core.llm.client import _failure_kind

    assert _failure_kind(ValueError("x")) == "ValueError"


async def test_answer_has_a_deadline_for_the_whole_response(monkeypatch, caplog) -> None:
    """OpenRouter, пока модель думает, шлёт пробелы: срок — на весь ответ, не на паузу."""

    async def thinking(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(10)
        return httpx.Response(200, json={})

    monkeypatch.setattr(settings, "llm_timeout_s", 0.05)
    llm = object.__new__(LLMClient)
    llm._client = AsyncOpenAI(
        base_url="http://llm.test/v1",
        api_key="k",
        max_retries=0,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(thinking)),
    )
    llm.model = "m"
    with pytest.raises(TimeoutError):
        await llm.complete("s", "u")
    assert "LLM m: сбой (таймаут)" in caplog.text
