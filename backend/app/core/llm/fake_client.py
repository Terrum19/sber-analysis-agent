"""Fake LLM для тестирования без реальной модели.

Реализует тот же интерфейс, что и LLMClient, но возвращает заранее
определённые ResearchPlan и Insight. Позволяет запускать полный
агентный цикл на CI без Ollama/vLLM.
"""

from typing import Any, TypeVar

import instructor
from openai import AsyncOpenAI
from pydantic import BaseModel

from app.config import settings

T = TypeVar("T", bound=BaseModel)


class FakeLLM:
    """Заглушка LLMClient с фиксированным ответом для любого вопроса."""

    def __init__(self) -> None:
        self.model = "fake-llm"
        self._client = AsyncOpenAI(
            base_url="http://localhost:1/v1",
            api_key="not-needed",
            timeout=settings.llm_timeout_s,
        )
        self._instructor = instructor.from_openai(self._client)

    async def complete(self, system: str, user: str, **kwargs: Any) -> str:
        """Возвращает фиксированную строку-ответ."""
        return "Fake LLM response"

    async def complete_structured(self, system: str, user: str, schema: type[T]) -> T:
        """Генерирует объект по схеме с фиксированными значениями.

        - ResearchPlan: 1 шаг (aggregate) + final_viz=bar
        - Insight: заголовок + детали + лимиты
        - Любая другая схема: пустой экземпляр через model_validate({})
        """
        if schema.__name__ == "ResearchPlan":
            from app.models.research_step import ResearchPlan, ResearchStep

            return schema(
                steps=[
                    ResearchStep(
                        step_id=0,
                        tool="aggregate",
                        columns=["category"],
                        needs_llm_codegen=False,
                        purpose="Агрегируем данные по категории",
                    )
                ],
                final_viz="bar",
                dashboard_requested=False,
            )
        if schema.__name__ == "Insight":
            return schema(
                headline="Исследование завершено",
                details=["Данные обработаны по запросу пользователя"],
                limits="Результаты основаны на доступных в таблице данных",
            )
        return schema.model_validate({})