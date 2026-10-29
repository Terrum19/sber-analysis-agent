"""Схемы дашборда по кнопке (#224): идеи, план плиток, выбор графика."""

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

# Сколько идей предлагаем и сколько плиток строим.
IDEAS_COUNT = 4
MIN_TILES = 4
MAX_TILES = 5

Intent = Literal["kpi", "compare", "trend", "distribution", "relation", "share"]
ChartType = Literal["bar", "line", "area", "scatter", "histogram", "box", "heatmap", "table", "kpi"]


class DashboardIdea(BaseModel):
    """Вариант дашборда: кнопка в чате и бриф для плана плиток."""

    id: str = ""
    title: str
    description: str = ""


class DashboardIdeas(BaseModel):
    ideas: list[DashboardIdea] = Field(default_factory=list)


class OutlineTile(BaseModel):
    """Плитка плана: вопрос, на который отвечает её шаг, и что она показывает."""

    title: str
    question: str
    intent: Intent = "compare"

    @field_validator("intent", mode="before")
    @classmethod
    def _unknown_is_compare(cls, value: Any) -> Any:
        """Модель придумала свой intent — это подсказка, а не ошибка."""
        allowed = Intent.__args__
        text = value.strip().lower() if isinstance(value, str) else value
        return text if text in allowed else "compare"


class DashboardOutline(BaseModel):
    """План дашборда: хватило ли запроса, название и плитки."""

    enough_info: bool = True
    title: str = ""
    tiles: list[OutlineTile] = Field(default_factory=list)


class ChartChoice(BaseModel):
    """Вид графика плитки по её готовому результату; x, y, color — колонки результата."""

    tile_id: int
    chart_type: ChartType = "table"
    x: str | None = None
    y: str | None = None
    color: str | None = None
    reason: str = ""

    @field_validator("chart_type", mode="before")
    @classmethod
    def _normalize(cls, value: Any) -> Any:
        text = value.strip().lower() if isinstance(value, str) else value
        return text if text in ChartType.__args__ else "table"


class ChartChoices(BaseModel):
    charts: list[ChartChoice] = Field(default_factory=list)
