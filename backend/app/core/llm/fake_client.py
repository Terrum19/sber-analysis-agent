"""Fake LLM для тестирования без реальной модели.

Реализует тот же интерфейс, что и LLMClient, но возвращает заранее
определённые ResearchPlan и Insight. Позволяет запускать полный
агентный цикл на CI без Ollama/vLLM.
"""

import re
from typing import Any, TypeVar

import instructor
from openai import AsyncOpenAI
from pydantic import BaseModel

from app.config import settings

T = TypeVar("T", bound=BaseModel)

PLAN_COLUMNS = ("category", "user_id")

# Дашборд по кнопке (#224): запрос короче стольких слов — «информации не хватает».
DASHBOARD_MIN_WORDS = 3
FAKE_TILES = (
    ("Всего записей", "Сколько всего записей", "kpi"),
    ("Записи по категориям", "Количество записей по category", "compare"),
    ("Записи по пользователям", "Количество записей по user_id", "compare"),
    ("Доли категорий", "Доля записей по category", "share"),
)


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

    async def warm_up(self) -> None:
        """Ничего: контекст сервера фейку не нужен."""

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
            from app.models.research_step import ResearchStep

            column = next((name for name in PLAN_COLUMNS if name in user), PLAN_COLUMNS[0])
            return schema(
                steps=[
                    ResearchStep(
                        step_id=0,
                        tool="aggregate",
                        columns=[column],
                        purpose="Агрегируем данные по категории",
                    )
                ],
                final_viz="bar",
                dashboard_requested=False,
            )
        if schema.__name__ in ("DashboardIdeas", "DashboardOutline", "ChartChoices"):
            return schema.model_validate(_fake_dashboard(schema.__name__, user))
        if schema.__name__ == "Insight":
            return schema(
                headline="Исследование завершено",
                details=["Данные обработаны по запросу пользователя"],
                limits="Результаты основаны на доступных в таблице данных",
            )
        return schema.model_validate({})


def _fake_dashboard(schema: str, user: str) -> dict:
    """Детерминированные идеи, план плиток и выбор графика для CI."""
    if schema == "DashboardIdeas":
        # Прежние варианты перечислены строками «- название»: новые их не повторяют
        taken = set(re.findall(r"^- (.+)$", user, re.MULTILINE))
        numbers = [number for number in range(1, 100) if f"Вариант {number}" not in taken][:4]
        return {
            "ideas": [
                {"title": f"Вариант {number}", "description": f"Описание варианта {number}"}
                for number in numbers
            ]
        }
    if schema == "DashboardOutline":
        request = re.search(r"Запрос: (.*)", user)
        words = len(request.group(1).split()) if request else 0
        if words < DASHBOARD_MIN_WORDS and "enough_info всегда true" not in user:
            return {"enough_info": False, "title": "", "tiles": []}
        return {
            "enough_info": True,
            "title": "Обзор данных",
            "tiles": [
                {"title": title, "question": question, "intent": intent}
                for title, question, intent in FAKE_TILES
            ],
        }
    tile_ids = [int(found) for found in re.findall(r"--- плитка (\d+):", user)]
    return {
        "charts": [
            {"tile_id": tile_id, "chart_type": "bar", "reason": "fake"} for tile_id in tile_ids
        ]
    }
