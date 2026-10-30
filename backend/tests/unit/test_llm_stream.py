"""LLMClient.complete_stream и защита _check_context от чтения SSE."""

import json

import httpx
import instructor
import openai
import pytest
from openai import AsyncOpenAI

from app.core.llm.client import LLMClient


def completion_chunk(delta: dict, finish_reason: str | None = None) -> dict:
    return {
        "id": "c1",
        "object": "chat.completion.chunk",
        "created": 0,
        "model": "m",
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
    }


def sse_body(chunks: list[dict]) -> bytes:
    data = b"".join(
        f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n".encode() for chunk in chunks
    )
    return data + b"data: [DONE]\n\n"


def client_with(handler) -> tuple[LLMClient, list[httpx.Request]]:
    requests: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return handler(request)

    llm = object.__new__(LLMClient)
    raw = AsyncOpenAI(
        base_url="http://llm.test/v1",
        api_key="k",
        max_retries=0,
        http_client=httpx.AsyncClient(
            transport=httpx.MockTransport(record),
            event_hooks={"response": [llm._check_context]},
        ),
    )
    llm._client, llm.model, llm._instructor = raw, "m", instructor.from_openai(raw)
    llm.last_prompt_tokens = None
    return llm, requests


class TrackedStream(httpx.AsyncByteStream):
    """Поток, который запоминает, читал ли его кто-нибудь."""

    def __init__(self) -> None:
        self.read = False

    async def __aiter__(self):
        self.read = True
        yield b"data: {}\n\n"


async def test_complete_stream_yields_content_deltas():
    body = sse_body(
        [
            completion_chunk({"content": "A"}),
            completion_chunk({"content": "B"}),
        ]
    )
    llm, requests = client_with(
        lambda _: httpx.Response(200, headers={"content-type": "text/event-stream"}, content=body)
    )

    chunks = [chunk async for chunk in llm.complete_stream("system", "user")]

    assert chunks == ["A", "B"]
    payload = json.loads(requests[0].content)
    assert payload["stream"] is True


async def test_complete_stream_skips_empty_and_usage_chunks():
    body = sse_body(
        [
            completion_chunk({"role": "assistant"}),
            completion_chunk({"content": "A"}),
            {
                "id": "c1",
                "object": "chat.completion.chunk",
                "created": 0,
                "model": "m",
                "choices": [],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            },
            completion_chunk({}, finish_reason="length"),
            completion_chunk({"content": "B"}),
        ]
    )
    llm, _ = client_with(
        lambda _: httpx.Response(200, headers={"content-type": "text/event-stream"}, content=body)
    )

    chunks = [chunk async for chunk in llm.complete_stream("system", "user")]

    assert chunks == ["A", "B"]


async def test_complete_stream_retries_empty_truncated_reasoning(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "llm_max_tokens", 16)
    monkeypatch.setattr(settings, "llm_length_retry_tokens", 256)
    bodies = [
        sse_body([completion_chunk({}, finish_reason="length")]),
        sse_body([completion_chunk({"content": "Ответ"})]),
    ]
    llm, requests = client_with(
        lambda _: httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=bodies.pop(0),
        )
    )

    chunks = [chunk async for chunk in llm.complete_stream("system", "user")]

    assert chunks == ["Ответ"]
    assert len(requests) == 2
    assert json.loads(requests[0].content)["max_tokens"] == 16
    assert json.loads(requests[1].content)["max_tokens"] == 256


async def test_complete_stream_sends_reasoning_and_provider(monkeypatch):
    from app.config import settings

    # Стрим должен слушать те же LLM_REASONING_EFFORT/LLM_PROVIDER, что и complete().
    monkeypatch.setattr(settings, "llm_reasoning_effort", "low")
    monkeypatch.setattr(settings, "llm_provider", "deepinfra")
    body = sse_body([completion_chunk({"content": "A"})])
    llm, requests = client_with(
        lambda _: httpx.Response(200, headers={"content-type": "text/event-stream"}, content=body)
    )

    assert [chunk async for chunk in llm.complete_stream("system", "user")] == ["A"]
    payload = json.loads(requests[0].content)
    assert payload["reasoning"] == {"effort": "low"}
    assert payload["provider"] == {"only": ["deepinfra"]}


async def test_complete_stream_raises_on_server_error():
    llm, _ = client_with(lambda _: httpx.Response(500, json={"error": {"message": "boom"}}))

    with pytest.raises(openai.APIStatusError):
        [chunk async for chunk in llm.complete_stream("system", "user")]


async def test_check_context_does_not_read_sse_body():
    llm, _ = client_with(lambda _: httpx.Response(200))
    request = httpx.Request(
        "POST",
        "http://llm.test/v1/chat/completions",
        json={"stream": True, "messages": [{"role": "user", "content": "u"}]},
    )
    stream = TrackedStream()
    response = httpx.Response(
        200,
        headers={"content-type": "text/event-stream"},
        stream=stream,
        request=request,
    )

    await llm._check_context(response)

    assert stream.read is False
    assert response.is_stream_consumed is False


async def test_check_context_does_not_read_stream_request_without_content_type():
    llm, _ = client_with(lambda _: httpx.Response(200))
    request = httpx.Request(
        "POST",
        "http://llm.test/v1/chat/completions",
        json={"stream": True, "messages": [{"role": "user", "content": "u"}]},
    )
    stream = TrackedStream()
    response = httpx.Response(200, stream=stream, request=request)

    await llm._check_context(response)

    assert stream.read is False
    assert response.is_stream_consumed is False
