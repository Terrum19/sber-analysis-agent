"""LLMClient: повторяется только невалидный ответ модели, а не 401/404 и не сеть."""

import json

import httpx
import instructor
import openai
import pytest
from openai import AsyncOpenAI
from pydantic import BaseModel

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

    raw = AsyncOpenAI(
        base_url="http://llm.test/v1",
        api_key="k",
        max_retries=0,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    llm = object.__new__(LLMClient)
    llm._client, llm.model, llm._instructor = raw, "m", instructor.from_openai(raw)
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
