"""Схемы дашборда по кнопке (#224): идеи, план плиток, выбор графика."""

import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# Сколько идей предлагаем и сколько плиток строим.
IDEAS_COUNT = 4
MIN_TILES = 4
MAX_TILES = 5

Intent = Literal["kpi", "compare", "trend", "distribution", "relation", "share"]
TileKind = Literal["simple", "complex"]
TileAgg = Literal["COUNT", "COUNT_DISTINCT", "SUM", "AVG", "MIN", "MAX", "MEDIAN"]
TilePeriod = Literal["day", "week", "month", "quarter", "year"]
ChartType = Literal["bar", "line", "area", "scatter", "histogram", "box", "heatmap", "table", "kpi"]


class DashboardIdea(BaseModel):
    """Вариант дашборда: кнопка в чате и бриф для плана плиток."""

    id: str = ""
    title: str
    description: str = ""


class DashboardIdeas(BaseModel):
    ideas: list[DashboardIdea] = Field(default_factory=list)


# Дополнительных показателей у простой плитки (вариант rich_tiles).
MAX_EXTRA_METRICS = 2


class TileMetric(BaseModel):
    """Ещё один показатель простой плитки: колонка или формула и агрегат."""

    metric: str | None = None
    agg: TileAgg = "SUM"
    expression: str | None = None

    @field_validator("agg", mode="before")
    @classmethod
    def _upper(cls, value: Any) -> Any:
        text = value.strip().upper() if isinstance(value, str) else value
        return text if text in TileAgg.__args__ else "SUM"

    @field_validator("metric", "expression", mode="before")
    @classmethod
    def _empty_is_none(cls, value: Any) -> Any:
        return None if isinstance(value, str) and not value.strip() else value


class OutlineTile(BaseModel):
    """Плитка плана: вопрос, на который отвечает её шаг, и что она показывает.

    Вариант quick_tiles: у простой плитки (kind=simple) план сразу отдаёт поля
    расчёта — шаги собирает код (core/dashboards/quick_tiles.py). Сложная
    плитка или неподходящие поля — обычный планировщик по question.

    Вариант rich_tiles добавляет expression — формулу показателя (metric —
    тогда имя новой колонки) и extra_metrics — ещё 1–2 показателя рядом.
    """

    title: str
    question: str
    intent: Intent = "compare"
    kind: TileKind = "complex"
    metric: str | None = None
    agg: TileAgg | None = None
    by: str | None = None
    period: TilePeriod | None = None
    date_column: str | None = None
    top: int | None = Field(default=None, ge=1, le=100)
    filter_column: str | None = None
    filter_value: str | int | float | bool | None = None
    expression: str | None = None
    extra_metrics: list[TileMetric] = Field(default_factory=list)

    @field_validator("kind", "agg", "period", mode="before")
    @classmethod
    def _unknown_is_none(cls, value: Any, info) -> Any:
        """Незнакомое значение поля — не ошибка: плитку построит планировщик."""
        if not isinstance(value, str):
            return value
        text = value.strip()
        if info.field_name == "agg":
            text = text.upper()
            return text if text in TileAgg.__args__ else None
        text = text.lower()
        if info.field_name == "kind":
            return text if text in TileKind.__args__ else "complex"
        return text if text in TilePeriod.__args__ else None

    @field_validator("extra_metrics", mode="before")
    @classmethod
    def _null_metrics(cls, value: Any) -> Any:
        return [] if value is None else value

    @field_validator("metric", "by", "date_column", "filter_column", "expression", mode="before")
    @classmethod
    def _empty_is_none(cls, value: Any) -> Any:
        return None if isinstance(value, str) and not value.strip() else value

    @field_validator("top", mode="before")
    @classmethod
    def _bad_top_is_none(cls, value: Any) -> Any:
        return value if isinstance(value, int) and 1 <= value <= 100 else None

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
