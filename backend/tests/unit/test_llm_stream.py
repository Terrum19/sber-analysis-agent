"""LLMClient.complete_stream: куски приходят по мере ответа, usage — в токены хода."""

import asyncio
import json

import httpx
from openai import AsyncOpenAI

from app.core.llm.client import LLMClient
from app.core.llm.usage import llm_scope, track_usage


def sse(payload: dict) -> bytes:
    return f"data: {json.dumps(payload)}\n\n".encode()


def delta(text: str) -> bytes:
    return sse(
        {
            "id": "c1",
            "object": "chat.completion.chunk",
            "created": 0,
            "model": "m",
            "choices": [{"index": 0, "delta": {"content": text}, "finish_reason": None}],
        }
    )


USAGE = sse(
    {
        "id": "c1",
        "object": "chat.completion.chunk",
        "created": 0,
        "model": "m",
        "choices": [],
        "usage": {"prompt_tokens": 120, "completion_tokens": 7, "total_tokens": 127},
    }
)


def client_with(body, requests: list[httpx.Request]) -> LLMClient:
    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=body)

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
    return llm


async def test_chunks_arrive_before_answer_ends() -> None:
    """Хук ответа не дочитывает стрим: первый кусок виден, пока сервер ещё пишет."""
    second = asyncio.Event()

    async def body():
        yield delta("Выручка ")
        await second.wait()
        yield delta("выросла")
        yield USAGE
        yield b"data: [DONE]\n\n"

    requests: list[httpx.Request] = []
    usage = track_usage()
    stream = client_with(body(), requests).complete_stream("s", "u")

    first = await asyncio.wait_for(anext(stream), 2)
    second.set()
    rest = [chunk async for chunk in stream]

    assert [first, *rest] == ["Выручка ", "выросла"]
    assert json.loads(requests[0].content)["stream_options"] == {"include_usage": True}
    assert (usage.llm_calls, usage.prompt_tokens, usage.completion_tokens) == (1, 120, 7)


async def test_stream_without_usage_still_counts_call() -> None:
    usage = track_usage()
    llm = client_with(delta("ок") + b"data: [DONE]\n\n", [])

    assert [chunk async for chunk in llm.complete_stream("s", "u")] == ["ок"]
    assert (usage.llm_calls, usage.prompt_tokens) == (1, 0)


async def test_complete_streams_inside_turn() -> None:
    """В ходе complete() идёт стримом: ответ тот же, куски двигают токены места хода."""
    requests: list[httpx.Request] = []
    llm = client_with(delta('{"a": ') + delta("1}") + USAGE + b"data: [DONE]\n\n", requests)
    sent: list[dict] = []
    usage = track_usage(sink=sent.append)

    with llm_scope(stage="planning"):
        assert await llm.complete("s", "u") == '{"a": 1}'

    assert json.loads(requests[0].content)["stream"] is True
    assert usage.as_event()["total_tokens"] == 127
    assert sent[-1]["data"] == {
        "stage": "planning",
        "key": "planning",
        "prompt_tokens": 120,
        "completion_tokens": 7,
        "live": False,
    }


async def test_complete_outside_turn_is_one_response() -> None:
    requests: list[httpx.Request] = []
    llm = client_with(b"", requests)
    llm._client = AsyncOpenAI(
        base_url="http://llm.test/v1",
        api_key="k",
        max_retries=0,
        http_client=httpx.AsyncClient(
            transport=httpx.MockTransport(lambda request: httpx.Response(200, json=COMPLETION))
        ),
    )
    assert await llm.complete("s", "u") == "ок"


async def test_hook_counts_tokens_of_plain_response() -> None:
    """Ответ без стрима (повторы instructor) считает хук ответа."""

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=COMPLETION)

    llm = client_with(b"", [])
    llm._client = AsyncOpenAI(
        base_url="http://llm.test/v1",
        api_key="k",
        max_retries=0,
        http_client=httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            event_hooks={"response": [llm._check_context]},
        ),
    )
    usage = track_usage()

    await llm._client.chat.completions.create(model="m", messages=[])
    assert usage.as_event() == {
        "llm_calls": 1,
        "prompt_tokens": 40,
        "completion_tokens": 2,
        "total_tokens": 42,
    }


COMPLETION = {
    "id": "c1",
    "object": "chat.completion",
    "created": 0,
    "model": "m",
    "choices": [
        {"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": "ок"}}
    ],
    "usage": {"prompt_tokens": 40, "completion_tokens": 2, "total_tokens": 42},
}


async def test_stream_calls_off_sends_one_response(monkeypatch) -> None:
    """LLM_STREAM_CALLS=false: в ходе complete() — одним ответом, токены считает хук."""
    from app.config import settings

    monkeypatch.setattr(settings, "llm_stream_calls", False)
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=COMPLETION)

    llm = client_with(b"", [])
    llm._client = AsyncOpenAI(
        base_url="http://llm.test/v1",
        api_key="k",
        max_retries=0,
        http_client=httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            event_hooks={"response": [llm._check_context]},
        ),
    )
    sent: list[dict] = []
    usage = track_usage(sink=sent.append)

    with llm_scope(stage="planning"):
        assert await llm.complete("s", "u") == "ок"

    assert "stream" not in json.loads(requests[0].content)
    assert usage.as_event()["total_tokens"] == 42
    assert sent[-1]["data"]["key"] == "planning" and not sent[-1]["data"]["live"]
