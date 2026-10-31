"""Схемы дашборда по кнопке (#224): идеи, план плиток, выбор графика."""

import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models.chart_choice import ChartPick

# Сколько идей предлагаем и сколько плиток строим.
IDEAS_COUNT = 4
MIN_TILES = 4
MAX_TILES = 5

Intent = Literal["kpi", "compare", "trend", "distribution", "relation", "share"]


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
    """План дашборда: хватило ли запроса, название и плитки.

    Все три ключа обязательны в JSON-схеме ответа: модель обрывала ответ
    после title, и без плиток план молча становился пустым. Разбор
    по-прежнему терпимый — без ключа tiles план пустой, а не ошибка.
    """

    model_config = ConfigDict(json_schema_extra={"required": ["enough_info", "title", "tiles"]})

    enough_info: bool = True
    title: str = ""
    tiles: list[OutlineTile] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _tiles_inside_title(cls, data: Any) -> Any:
        """Модель закрыла title не той кавычкой, и плитки попали в его текст.

        Пример: "title": "Товары…',\n  \"tiles\": [{…}]\n}" — достаём массив
        плиток из строки, а title обрезаем до сбоя.
        """
        if (
            not isinstance(data, dict)
            or data.get("tiles")
            or not isinstance(data.get("title"), str)
        ):
            return data
        title = data["title"]
        start = title.find('"tiles"')
        bracket = title.find("[", start)
        if start < 0 or bracket < 0:
            return data
        try:
            tiles, _ = json.JSONDecoder().raw_decode(title, bracket)
        except ValueError:
            return data
        if not isinstance(tiles, list):
            return data
        return {**data, "title": title[:start].rstrip(" ,\n'\""), "tiles": tiles}


class ChartChoice(ChartPick):
    """Вид графика плитки по её готовому результату; x, y, color — колонки результата."""

    tile_id: int


class ChartChoices(BaseModel):
    charts: list[ChartChoice] = Field(default_factory=list)
