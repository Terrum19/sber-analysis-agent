"""Единый клиент к локальной LLM (OpenAI-совместимый протокол).

Замкнутый контур: по умолчанию Ollama/vLLM на localhost.
Менять провайдера — только через config.settings.llm_base_url/model.

Если переменная окружения USE_FAKE_LLM=true, конструктор возвращает
FakeLLM вместо реального клиента — для тестирования на CI без локальной модели.
"""

import logging
import os
from typing import Any, TypeVar

import instructor
from openai import AsyncOpenAI
from pydantic import BaseModel

from app.config import settings

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


class LLMClient:
    """Обёртка над AsyncOpenAI для локального инференса Qwen."""

    def __new__(cls) -> "LLMClient":
        if os.getenv("USE_FAKE_LLM", "false").lower() == "true":
            from app.core.llm.fake_client import FakeLLM

            return FakeLLM()
        return super().__new__(cls)

    def __init__(self) -> None:
        self._client = AsyncOpenAI(
            base_url=settings.llm_base_url,
            api_key="not-needed",
            timeout=settings.llm_timeout_s,
        )
        self.model = settings.llm_model
        self._instructor = instructor.from_openai(self._client)

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

        Использует instructor.from_openai для гарантированно валидного
        вывода по заданной Pydantic-схеме.

        Ollama через Instructor в режиме tools иногда возвращает
        несколько tool_calls (AssertionError "multiple tool calls").
        В этом случае делаем fallback: запрашиваем список схем и берём
        первый элемент; если и это не помогло — используем JSON-режим.
        """
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        # Первичная попытка: один объект схемы через tools
        try:
            return await self._instructor.chat.completions.create(
                model=self.model,
                messages=messages,
                response_model=schema,
                temperature=settings.llm_temperature,
            )
        except Exception as exc:  # noqa: BLE001
            if "multiple tool calls" not in str(exc):
                raise

        # Fallback 1: запрашиваем список объектов, берём первый
        try:
            results = await self._instructor.chat.completions.create(
                model=self.model,
                messages=messages,
                response_model=list[schema],  # type: ignore[valid-type]
                temperature=settings.llm_temperature,
            )
            if results:
                return results[0]
        except Exception:  # noqa: BLE001
            pass

        # Fallback 2: JSON-режим без tools, парсим вручную
        raw = await self.complete(
            system=system,
            user=user + "\n\nОтветь только валидным JSON, соответствующим схеме модели.",
        )
        return schema.model_validate_json(raw)
