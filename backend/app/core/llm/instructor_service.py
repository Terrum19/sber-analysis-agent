"""Structured output через Instructor + Pydantic.

Гарантирует, что вывод LLM (ResearchStep, Insight, DashboardTile)
валиден по схеме. Невалидный вывод -> ретрай (см. agent/recovery.py).
"""

from pydantic import BaseModel

from app.core.llm.client import LLMClient


class InstructorService:
    """Фасад structured-вывода поверх LLMClient."""

    def __init__(self, client: LLMClient | None = None) -> None:
        self._client = client or LLMClient()

    async def generate(self, system: str, user: str, schema: type[BaseModel]) -> BaseModel:
        """Сгенерировать объект, валидированный по Pydantic-схеме."""
        return await self._client.complete_structured(system, user, schema)
