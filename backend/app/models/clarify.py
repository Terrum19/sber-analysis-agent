"""Уточняющие вопросы глубокого исследования (A-28).

ClarifyAnswer — ответ модели: хватает ли вопроса для исследования и, если нет,
вопросы с вариантами. У варианта модель перечисляет колонки, на которые он
опирается: по ним код отбрасывает варианты с выдуманными колонками. Наружу
(в API) варианты уходят строками.

Clarification — ответ пользователя на вопрос: приходит в ChatRequest и
хранится в журнале хода. axis — тема вопроса: по ней инсайт находит цель.
"""

from collections.abc import Sequence
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.models.research_step import NOTE_LIMIT

Axis = Literal["goal", "metric", "period", "breakdown", "filter", "format"]

# Сколько вопросов показываем и сколько вариантов у вопроса.
MIN_QUESTIONS = 3
MAX_QUESTIONS = 4
# Меньше MIN_QUESTIONS годных вопросов — переспрос; после всех попыток хватает и стольких.
MIN_KEPT_QUESTIONS = 2
MIN_OPTIONS = 2
MAX_OPTIONS = 4
# Уточнений в одном ходе — не больше, чем осей.
MAX_CLARIFICATIONS = len(Axis.__args__)

ClarifyText = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=NOTE_LIMIT)
]


class ClarifyOption(BaseModel):
    """Вариант ответа: текст для кнопки и колонки таблицы, на которые он опирается."""

    model_config = ConfigDict(json_schema_extra={"required": ["text", "columns"]})

    text: str
    columns: list[str] = Field(default_factory=list)


class ClarifyQuestion(BaseModel):
    """Вопрос по одной теме.

    Все ключи обязательны в JSON-схеме ответа: без options модель дописывала
    в text продолжение промпта вместо вариантов. Разбор по-прежнему терпимый.
    """

    model_config = ConfigDict(json_schema_extra={"required": ["axis", "text", "options", "multi"]})

    axis: Axis
    text: str
    options: list[ClarifyOption] = Field(default_factory=list)
    multi: bool = False


class ClarifyAnswer(BaseModel):
    """Ответ модели: enough_info — вопрос однозначен, иначе вопросы по осям."""

    model_config = ConfigDict(json_schema_extra={"required": ["enough_info", "questions"]})

    enough_info: bool = False
    questions: list[ClarifyQuestion] = Field(default_factory=list)


class Clarification(BaseModel):
    """Ответ пользователя на уточняющий вопрос."""

    question: ClarifyText
    answer: ClarifyText
    axis: Axis | None = None


def describe_clarifications(clarifications: Sequence[dict]) -> str:
    """Уточнения одной строкой: «вопрос — ответ; …»; цель помечена."""
    return "; ".join(
        f"{item['question']} — {item['answer']}" + (" (цель)" if item.get("axis") == "goal" else "")
        for item in clarifications
    )
