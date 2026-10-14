"""Единый клиент к локальной LLM (OpenAI-совместимый протокол).

Замкнутый контур: по умолчанию Ollama/vLLM на localhost.
Менять провайдера — только через config.settings.llm_base_url/model.
"""
import json
from typing import Any, TypeVar

from openai import AsyncOpenAI

from app.config import settings
from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


class LLMClient:
    """Обёртка над AsyncOpenAI для локального инференса Qwen."""

    def __init__(self) -> None:
        self._client = AsyncOpenAI(
            base_url=settings.llm_base_url,
            api_key="not-needed",  # локальный сервер ключа не требует
            timeout=settings.llm_timeout_s,
        )
        self.model = settings.llm_model

    async def complete(self, system: str, user: str, **kwargs: Any) -> str:
        """Одиночный completion (без structured output)."""
        resp = await self._client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=settings.llm_temperature,
            **kwargs,
        )
        return resp.choices[0].message.content or ""

    async def complete_structured(self, system: str, user: str, schema: type[T]) -> T:
        """Structured output через Instructor (валидация по Pydantic-схеме).

        TODO(round-2): переключить на instructor.from_openai(...).create(...)
        """
        raw = await self.complete(system, user)
        return schema.model_validate(json.loads(_extract_json(raw)))


def _extract_json(text: str) -> str:
    """Вырезает JSON из ответа LLM, даже если обёрнут в ```json ... ```."""
    text = text.strip()
    if "```" in text:
        parts = text.split("```")
        for part in parts:
            part = part.removeprefix("json").strip()
            if part.startswith(("{", "[")):
                return part
    start = min((i for i in (text.find("{"), text.find("[")) if i != -1), default=-1)
    end = max(text.rfind("}"), text.rfind("]"))
    return text[start : end + 1] if start != -1 and end > start else text
